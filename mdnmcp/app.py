import logging

import asab.api
import asab.web.rest


import asab.mcp

from .handler_mcp import MarkdownNotesMCPHandler

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


class MarkdownNotesMCPApplication(asab.Application):

	def __init__(self):
		super().__init__()

		# Create the Web server
		web = asab.web.create_web_server(self, api=True)

		# Initialize the Tenant service
		self.TenantService = asab.web.tenant.TenantService(self)

		# Add the MCP service, it will be used to register tools and resources
		self.MCPService = asab.mcp.MCPService(self, web, name="markdown-notes-mcp", version="25.11.0")

		# Add the Markdown notes handler, it will be used to register tools and resources for the Markdown notes
		self.MCPHandler = MarkdownNotesMCPHandler(self)
