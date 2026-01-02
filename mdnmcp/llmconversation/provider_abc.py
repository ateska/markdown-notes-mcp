import abc
import logging
import aiohttp

from .datamodel import Conversation, Exchange

L = logging.getLogger(__name__)

class LLMChatProviderABC(abc.ABC):
	def __init__(self, service, *, url):
		self.LLMChatService = service
		self.URL = url.rstrip('/') + '/'
		self.Models = []  # Cached list of models

	@abc.abstractmethod
	def prepare_headers(self):
		pass

	@abc.abstractmethod
	async def chat_request(self, conversation: Conversation, exchange: Exchange):
		pass

	async def get_models(self):
		'''
		Get the list of models from the LLM chat provider.
		Implements /v1/models call that works with vLLM, tensorrm-llm, OpenAI and Anthropic API and possibly other LLM chat providers.
		'''

		async with aiohttp.ClientSession(headers=self.prepare_headers()) as session:
			try:
				async with session.get(self.URL + "v1/models") as response:
					if response.status != 200:
						if response.status == 401 and response.content_type == "application/json":
							resp = await response.json()
							L.warning("Unauthorized access to LLM chat provider", struct_data={"url": self.URL, "response": resp})
							return None
						L.warning("Error getting models", struct_data={"status": response.status, "text": await response.text()})
						return None

					resp = await response.json()
					models = resp['data']
					if self.URL.startswith('https://api.openai.com/'):
						# Filter only GPT models from OpenAI API
						# They offer more models but they are not directly usable for chat.
						models = filter(lambda model: model['owned_by'] == 'openai', models)
					self.Models = models
					return [model['id'] for model in self.Models]

			except aiohttp.ClientError as e:
				L.warning("Error communicating with LLM: {} {}".format(e.__class__.__name__, e), struct_data={"url": self.URL})
				return None

		return []
