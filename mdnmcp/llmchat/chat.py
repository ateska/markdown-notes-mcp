import typing
import datetime
import dataclasses


@dataclasses.dataclass
class ChatMessage:
	Role: str
	Content: str
	CreatedAt: datetime.datetime = datetime.datetime.now(datetime.timezone.utc)

@dataclasses.dataclass
class ChatRequest:
	Message: dict
	CreatedAt: datetime.datetime = datetime.datetime.now(datetime.timezone.utc)

@dataclasses.dataclass
class ChatResponse:
	Event: list[tuple[typing.Literal['data', 'event', '???'], dict|str|bytes]]
	CreatedAt: datetime.datetime = datetime.datetime.now(datetime.timezone.utc)


@dataclasses.dataclass
class Chat:

	def __init__(self, id: str):
		self.Id = id
		self.Messages = []
		self.History = []
		self.CreatedAt = datetime.datetime.now(datetime.timezone.utc)

	Id: str
	Messages: list[ChatMessage]
	CreatedAt: datetime.datetime

