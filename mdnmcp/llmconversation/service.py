import uuid
import random
import asyncio
import logging

import asab

from .datamodel import Conversation, UserMessage, Exchange, FunctionCall
from .tool_ping import tool_ping

from .provider_v1response import LLMChatProviderV1Response
from .provider_v1messages import LLMChatProviderV1Messages


#

L = logging.getLogger(__name__)

#

# content: `You are a helpful assistant within the application 'Markdown notes' that is used to write Markdown notes.
# The user is a writer of Markdown note named '${notePath}'.
# You are helping the user to write the note by providing review, suggestions, corrections and other feedback.
# Use the tools to work with the note.
# There is a whole directory of notes in the application, you can use the tools to work with the notes.
# Use the GitHub Flavored Markdown syntax to format your responses.`


Instructions = " ".join([
	"You are a helpful assistant with access to tools.",
	"You must call a function 'ping' when asked to ping any host or server.",
	"You may use available tools to fulfill requests.",
	"Always use the GitHub Flavored Markdown syntax to format your responses. Don't enclose the response in backticks if it's not a code block.",
	"Always use preformatted text for reasoning.",
	"You must respond in the same language as the user's message.",
])

class LLMConversationRouterService(asab.Service):


	def __init__(self, app, service_name="LLMConversationRouterService"):
		super().__init__(app, service_name)

		self.Providers = []
		self.Conversations = dict[str, Conversation]()

		self.load_providers()


	def load_providers(self):
		for section in asab.Config.sections():
			if not section.startswith("provider:"):
				continue

			ptype = asab.Config[section].get('type')
			match ptype:
				case 'LLMChatProviderV1Response':
					self.Providers.append(LLMChatProviderV1Response(self, **asab.Config[section]))
				case 'LLMChatProviderV1Messages':
					self.Providers.append(LLMChatProviderV1Messages(self, **asab.Config[section]))
				case _:
					L.warning("Unknown provider type, skipping", struct_data={"type": ptype})


	async def create_conversation(self):
		while True:
			conversation_id = 'conversation-' + uuid.uuid4().hex
			if conversation_id in self.Conversations:
				continue
			break

		L.log(asab.LOG_NOTICE, "New conversation created", struct_data={"conversation_id": conversation_id})

		conversation = Conversation(conversation_id=conversation_id, instructions=Instructions)
		self.Conversations[conversation.conversation_id] = conversation
		return conversation


	async def get_conversation(self, conversation_id, create=False):
		conversation = self.Conversations.get(conversation_id)
		if conversation is None and create:
			conversation = await self.create_conversation(conversation_id)
		return conversation


	async def create_exchange(self, conversation: Conversation, item: UserMessage) -> None:
		new_exchange = Exchange()
		conversation.exchanges.append(new_exchange)

		new_exchange.items.append(item)
		await self.send_update(conversation, {
			"type": "item.appended",
			"item": item.to_dict(),
		})

		await self.schedule_task(conversation, new_exchange, self.task_chat_request)


	async def schedule_task(self, conversation: Conversation, exchange: Exchange, task, *args, **kwargs) -> None:
		t = asyncio.create_task(
			task(conversation, exchange, *args, **kwargs),
			name=f"conversation-{conversation.conversation_id}-task"
		)

		def on_task_done(task):
			conversation.tasks.remove(task)
			asyncio.create_task(self.send_update_tasks(conversation))

		t.add_done_callback(on_task_done)

		conversation.tasks.append(t)
		await self.send_update_tasks(conversation)
		

	async def send_update_tasks(self, conversation: Conversation) -> None:
		await self.send_update(
			conversation,
			{
				"type": "tasks.updated",
				"count": len(conversation.tasks),
			}
		)


	async def task_chat_request(self, conversation: Conversation, exchange: Exchange) -> None:
		model = conversation.get_model()
		assert model is not None, "Model is not set"

		# Find and select a provider for the model
		providers = [provider for provider in self.Providers if model in set(model['id'] for model in provider.Models)]
		assert len(providers) > 0, "No provider found for model"
		provider = random.choice(providers)

		async def print_waiting():
			while True:
				await asyncio.sleep(1)
				# TODO: Indicate waiting for a model in the UI
				print("Waiting for a model ...")

		waiting_task = asyncio.create_task(print_waiting())
		try:
			async with provider.Semaphore:
				waiting_task.cancel()
				await provider.chat_request(conversation, exchange)
		finally:
			waiting_task.cancel()
			

	async def get_models(self):
		models = []

		async def collect_models(models, provider):
			try:
				pmodels = await provider.get_models()
			except Exception as e:
				L.exception("Error collecting models", struct_data={"provider": provider.__class__.__name__})
				return

			if pmodels is not None:
				models.extend(pmodels)

		async with asyncio.TaskGroup() as tg:
			for provider in self.Providers:
				tg.create_task(collect_models(models, provider))

		return models


	async def send_update(self, conversation: Conversation, event: dict):
		async with asyncio.TaskGroup() as tg:
			for monitor in conversation.monitors:
				tg.create_task(monitor(event))


	async def send_full_update(self, conversation: Conversation, monitor):
		items = []
		full_update = {
			"type": "update.full",
			"conversation_id": conversation.conversation_id,
			"created_at": conversation.created_at.isoformat(),
			"items": items,
		}

		for exchange in conversation.exchanges:
			for item in exchange.items:
				match item.__class__:
					case UserMessage:
						items.append(item.to_dict())

		try:
			await monitor(full_update)
		except Exception:
			L.exception("Error sending full update to monitors", struct_data={"conversation_id": conversation.conversation_id})


	async def create_function_call(self, conversation: Conversation, function_call: FunctionCall):
		await self.schedule_task(conversation, function_call, self.task_function_call)
		

	async def task_function_call(self, conversation: Conversation, function_call: FunctionCall) -> None:
		L.log(asab.LOG_NOTICE, "Calling function ...", struct_data={"name": function_call.name})

		function_call.status = 'executing'
		await self.send_update(conversation, {
			"type": "item.updated",
			"item": function_call.to_dict(),
		})

		try:
			match function_call.name:

				case "ping":
					async for _ in tool_ping(function_call):
						await self.send_update(conversation, {
							"type": "item.updated",
							"item": function_call.to_dict(),
						})

				case _:
					L.warning("Unknown function call", struct_data={"name": function_call.name})
					function_call.content = f"Called unknown function '{function_call.name}', no result available."
					function_call.error = True

		except Exception as e:
			L.exception("Error in function call", struct_data={"name": function_call.name})
			function_call.content = "Generic exception occurred. Try again."
			function_call.error = True

		finally:
			function_call.status = 'finished'
			await self.send_update(conversation, {
				"type": "item.updated",
				"item": function_call.to_dict(),
			})

			# Initialize a new exchange with LLM
			new_exchange = Exchange()
			conversation.exchanges.append(new_exchange)
			await self.schedule_task(conversation, new_exchange, self.task_chat_request)
