import os
import asab.web.rest

from .utils import NOTE_EXTENSION

class MarkdownNotesWebHandler():

	def __init__(self, app, web):
		self.App = app
		web.add_get(r"/{tenant}/notes/{directory:.*}", self.list_notes)

	def list_notes(self, request):
		tenant = asab.contextvars.Tenant.get()
		directory = request.match_info.get("directory", "")

		directory_path = self.App.normalize_note_path(directory, tenant)
		if directory_path is None:
			raise ValueError("Path is not within the notes directory")

		notes = []
		directories = []
		for item in os.listdir(directory_path):
			if item.endswith(NOTE_EXTENSION) and not item.startswith('.'):
				notes.append(item)
			elif os.path.isdir(os.path.join(directory_path, item)) and not item.startswith('.'):
				directories.append(item)

		data = {
			"result": "OK",
			"data": {
				"notes": notes,
				"directories": directories,
			},
		}

		return asab.web.rest.json_response(request, data)
