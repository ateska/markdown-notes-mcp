import os
import logging

import asab.web.rest
import asab.contextvars

from .handler_web import MarkdownNotesWebHandler

#

L = logging.getLogger(__name__)

#

asab.Config.add_defaults({
	"general": {
		"notes": "notes",
	},
	"web": {
		"listen": "8898",
	},
})


class MarkdownNotesApplication(asab.Application):

	def __init__(self):
		super().__init__()

		self.NotesDirectory = asab.Config.get("general", "notes", fallback="notes")
		os.makedirs(self.NotesDirectory, exist_ok=True)

		# Create the Web server
		web = asab.web.create_web_server(self, api=True)

		# Initialize the Tenant service
		self.TenantService = asab.web.tenant.TenantService(self)

		self.WebHandler = MarkdownNotesWebHandler(self, web)


	def normalize_note_path(self, user_path, tenant = None):
		'''
		Normalize the path to be within the base path.
		'''

		if tenant is None:
			tenant = asab.contextvars.Tenant.get()
		assert tenant is not None

		while user_path.startswith('/'):
			user_path = user_path[1:]

		abs_base = os.path.abspath(os.path.join(self.NotesDirectory, tenant))
		abs_user = os.path.abspath(os.path.join(abs_base, user_path))

		if os.path.commonpath([abs_base, abs_user]) == abs_base:
			return abs_user

		else:
			return None
