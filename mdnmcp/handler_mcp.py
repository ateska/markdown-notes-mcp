import os
import logging
import urllib.parse

import asab
import asab.mcp
import asab.contextvars


L = logging.getLogger(__name__)


NOTE_URI_PREFIX = "note://"
PICTURE_URI_PREFIX="img://"
NOTE_MIME_TYPE = "text/markdown"
NOTE_EXTENSION = ".md"
PICTURE_EXTENSIONS = {".jpg", ".png", ".gif"}


class MarkdownNotesMCPHandler():

	def __init__(self, app):
		self.App = app

		self.NotesDirectory = asab.Config.get("general", "notes", fallback="notes")

		os.makedirs(self.NotesDirectory, exist_ok=True)

		self.App.MCPService.add_tool(self.tool_create_or_update_note)
		self.App.MCPService.add_tool(self.tool_delete_note)
		self.App.MCPService.add_tool(self.tool_read_note)

		self.App.MCPService.add_tool(self.tool_upload_picture)

		self.App.MCPService.add_tool(self.tool_list_notes)

		self.App.MCPService.add_resource_template(self.resource_template_notes)
		self.App.MCPService.add_resource_list(NOTE_URI_PREFIX, self.resource_list_notes)

		self.App.MCPService.add_instruction("MarkdownNotesMCPHandler", 
			f"You are using the `{self.App.MCPService.Name}` `{self.App.MCPService.Version}` MCP server."
			"This MCP server can be used to manage Markdown notes. You can create, update, delete, and read Markdown notes."
			"Markdown notes are stored in the directory structure of the notes directory."
		)


	@asab.mcp.mcp_tool(
		name="create_or_update_note",
		title="Create or update a note",
		description="""
			Create a new Markdown note or update an existing Markdown note at the given path with the provided content.
			
			The path parameter specifies the note location:
			- Can include subdirectories separated by '/' (e.g., "projects/meeting-notes")
			- The '.md' extension is automatically appended if not provided
			- Leading slashes are normalized
			- Subdirectories are automatically created if they don't exist
			- Paths containing '..' are not allowed for security reasons
			
			The content parameter should contain valid Markdown text.
			
			Returns a resource link that can be used to reference the created or updated note.
		""",
		inputSchema={
			"type": "object",
			"properties": {
				"path": {"type": "string"},
				"content": {"type": "string", "format": "markdown"},
			},
		},
	)
	async def tool_create_or_update_note(self, path, content):
		tenant = asab.contextvars.Tenant.get()

		if not path.endswith(NOTE_EXTENSION):
			path += NOTE_EXTENSION

		note_path = _normalize_path(self.NotesDirectory, path, tenant)
		if note_path is None:
			raise ValueError("Path is not within the notes directory")

		os.makedirs(os.path.dirname(note_path), exist_ok=True)

		new_note = not os.path.isfile(note_path)
		with open(note_path, "w") as f:
			f.write(content)

		if new_note:
			L.log(asab.LOG_NOTICE, "Created a new Markdown note", struct_data={"path": path})
		else:
			L.log(asab.LOG_NOTICE, "Updated a Markdown note", struct_data={"path": path})

		return asab.mcp.MCPToolResultResourceLink(
			uri=f"{NOTE_URI_PREFIX}/{path}",
			name=path,
			description=f"{'Created' if new_note else 'Updated'} a Markdown note",
			mimeType=NOTE_MIME_TYPE,
		)

	@asab.mcp.mcp_tool(
		name="delete_note",
		title="Delete a note",
		description="""
			Delete a Markdown note at the specified path.
			
			The path parameter:
			- Can include subdirectories separated by '/' (e.g., "projects/old-note")
			- The '.md' extension is automatically appended if not provided
			- Leading slashes are normalized
			- Paths containing '..' are not allowed for security reasons
			
			Note: Only the note file is deleted. Empty subdirectories are left intact.
			If the note does not exist, an error will be raised.
			
			Returns a confirmation message indicating successful deletion.
		""",
		inputSchema={
			"type": "object",
			"properties": {
				"path": {"type": "string"},
			},
		},
	)
	async def tool_delete_note(self, path):
		tenant = asab.contextvars.Tenant.get()

		if not path.endswith(NOTE_EXTENSION):
			path += NOTE_EXTENSION

		note_path = _normalize_path(self.NotesDirectory, path, tenant)
		if note_path is None:
			raise ValueError("Path is not within the notes directory")

		if not os.path.isfile(note_path):
			raise ValueError(f"Note '{path}' does not exist. Use 'list_notes' to see available notes.")

		os.remove(note_path)

		L.log(asab.LOG_NOTICE, "Deleted a Markdown note", struct_data={"path": path})

		return f"Successfully deleted note: {path}"


	@asab.mcp.mcp_tool(
		name="list_notes",
		title="List notes in a directory, optionally including directories",
		description="""
			List all Markdown notes (.md files) in the specified directory, optionally including directories.
			
			The directory parameter:
			- Use an empty string or '/' to list notes in the root notes directory
			- Can include subdirectories separated by '/' (e.g., "projects/2024")
			- Leading slashes are normalized
			- Paths containing '..' are not allowed for security reasons
			- Only lists direct children (does not recursively search subdirectories)
			- Hidden files (starting with '.') are excluded

			The directories parameter:
			- If True, the list will include directories, can be used to list directories recursively
			- If False, the list will only include notes
			- If not provided, the list will only include notes
			
			Returns:
			- A text summary listing all notes found in the directory
			- Resource links for each note that can be used with 'read_note' or other tools
			- The resource link URI or name field can be used as the path parameter in other operations
			
			If the directory is empty or doesn't exist, an appropriate message will be returned.
		""",
		inputSchema={
			"type": "object",
			"properties": {
				"directory": {"type": "string", "default": ""},
				"directories": {"type": "boolean", "default": False},
			},
		},
	)
	async def tool_list_notes(self, directory='', directories=False):
		tenant = asab.contextvars.Tenant.get()

		directory_path = _normalize_path(self.NotesDirectory, directory, tenant)
		if directory_path is None:
			raise ValueError("Path is not within the notes directory")

		if not os.path.isdir(directory_path):
			raise ValueError(f"Directory '{directory}' does not exist. Use an empty string to list the root directory.")

		notes = list(note for note in os.listdir(directory_path) if note.endswith(NOTE_EXTENSION) and not note.startswith('.'))

		if directory == "":
			dir_display = "root directory"
		else:
			dir_display = f"directory '{directory}'"
		
		if len(notes) == 0:
			summary = f"No Markdown notes found in {dir_display}.\n"
		else:
			summary = f"Found {len(notes)} note{'s' if len(notes) != 1 else ''} in {dir_display}:\n\n"
			for note in sorted(notes):
				summary += f" * `{note}`\n"

		if directories:
			dirlist = list(dir for dir in os.listdir(directory_path) if os.path.isdir(os.path.join(directory_path, dir)) and not dir.startswith('.'))
			summary += f"\nFound {len(dirlist)} director{'ies' if len(dirlist) != 1 else 'y'} in {dir_display}:\n\n"
			for directory in sorted(dirlist):
				summary += f" * `{directory}`\n"

		# Build URIs correctly, handling empty directory case
		if directory:
			uri_prefix = f"{NOTE_URI_PREFIX}/{directory}"
			name_prefix = f"{directory}/"
		else:
			uri_prefix = NOTE_URI_PREFIX
			name_prefix = ""
		
		return [
			asab.mcp.MCPToolResultTextContent(text=summary)
		] + [
			asab.mcp.MCPToolResultResourceLink(
				uri=f"{uri_prefix}/{note}",
				name=f"{name_prefix}{note}",
				description=f"Markdown note: {name_prefix}{note}",
				mimeType=NOTE_MIME_TYPE,
			) for note in sorted(notes)
 		]


	@asab.mcp.mcp_tool(
		name="read_note",
		title="Read a note",
		description="""
			Read and return the full content of a Markdown note at the specified path.
			
			The path parameter:
			- Can include subdirectories separated by '/' (e.g., "projects/meeting-notes")
			- The '.md' extension is automatically appended if not provided
			- Leading slashes are normalized
			- Paths containing '..' are not allowed for security reasons
			
			Returns the raw Markdown content of the note as a string.
			If the note does not exist, an error will be raised with a suggestion to use 'list_notes' to find available notes.
		""",
		inputSchema={
			"type": "object",
			"properties": {
				"path": {"type": "string"},
			},
		},
	)
	async def tool_read_note(self, path):
		tenant = asab.contextvars.Tenant.get()

		if not path.endswith(NOTE_EXTENSION):
			path += NOTE_EXTENSION

		note_path = _normalize_path(self.NotesDirectory, path, tenant)
		if note_path is None:
			raise ValueError("Path is not within the notes directory")

		if not os.path.isfile(note_path):
			raise ValueError(f"Note '{path}' does not exist. Use 'list_notes' to see available notes.")

		with open(note_path, "r") as f:
			content = f.read()

		return content


	@asab.mcp.mcp_tool(
		name="upload_picture",
		title="Upload a picture",
		description=f"""
			Upload an image file to the notes directory.
			
			The path parameter:
			- Must include a filename with one of the supported extensions: {', '.join(sorted(PICTURE_EXTENSIONS))}
			- Can include subdirectories separated by '/' (e.g., "images/screenshots/example.png")
			- Subdirectories are automatically created if they don't exist
			- Leading slashes are normalized
			- Paths containing '..' are not allowed for security reasons
			
			The content parameter should contain the binary image data (base64-encoded when transmitted).
			
			Supported image formats: {', '.join(sorted(PICTURE_EXTENSIONS))}
			
			Returns a resource link that can be used to reference the uploaded image.
		""",
		inputSchema={
			"type": "object",
			"properties": {
				"path": {"type": "string"},
				"content": {"type": "string", "format": "binary"},
			},
		},
	)
	async def tool_upload_picture(self, path, content):
		tenant = asab.contextvars.Tenant.get()

		path = _normalize_path(self.NotesDirectory, path, tenant)
		if path is None:
			raise ValueError("Path is not within the notes directory")

		if not any(path.endswith(ext) for ext in PICTURE_EXTENSIONS):
			extensions_list = ', '.join(sorted(PICTURE_EXTENSIONS))
			raise ValueError(f"Unsupported picture extension. The path must end with one of: {extensions_list}")

		os.makedirs(os.path.dirname(path), exist_ok=True)
		with open(path, "wb") as f:
			f.write(content)

		# Determine MIME type based on extension
		mime_type = None
		if path.endswith(".png"):
			mime_type = "image/png"
		elif path.endswith(".jpg"):
			mime_type = "image/jpeg"
		elif path.endswith(".gif"):
			mime_type = "image/gif"
		assert mime_type is not None, f"Unsupported picture extension: {path}"
		
		return asab.mcp.MCPToolResultResourceLink(
			uri=f"{PICTURE_URI_PREFIX}/{path}",
			name=path,
			description=f"Uploaded image: {path}",
			mimeType=mime_type,
		)


	@asab.mcp.mcp_resource_template(
		uri_prefix=NOTE_URI_PREFIX,
		uri_template=f"{NOTE_URI_PREFIX}/{{path*}}.md",
		name="notes",
		title="Markdown notes",
		description="Markdown notes stored in directories",
		mimeType=NOTE_MIME_TYPE
	)
	async def resource_template_notes(self, uri):
		'''
		Read the content of a note resource identified by its URI.
		Returns the note content or None if the note doesn't exist.
		'''

		uri_parsed = urllib.parse.urlparse(uri)
		if uri_parsed.scheme != NOTE_URI_PREFIX[:-3]:
			raise ValueError(f"Invalid URI scheme: {uri_parsed.scheme}; must be '{NOTE_URI_PREFIX}'")

		if uri_parsed.netloc != '':
			raise ValueError(f"Invalid URI netloc: {uri_parsed.netloc}; must be empty")

		if uri_parsed.params != '':
			raise ValueError(f"Invalid URI params: {uri_parsed.params}; must be empty")

		if uri_parsed.query != '':
			raise ValueError(f"Invalid URI query: {uri_parsed.query}; must be empty")

		if uri_parsed.fragment != '':
			raise ValueError(f"Invalid URI fragment: {uri_parsed.fragment}; must be empty")

		note_path = urllib.parse.unquote(uri_parsed.path)
		tenant = asab.contextvars.Tenant.get()

		if not note_path.endswith(NOTE_EXTENSION):
			note_path += NOTE_EXTENSION

		note_path = _normalize_path(self.NotesDirectory, note_path, tenant)
		if note_path is None:
			raise ValueError("Path is not within the notes directory")

		if not os.path.isfile(note_path):
			L.warning("Note not found", struct_data={"uri": uri})
			return None

		with open(note_path, "r") as f:
			content = f.read()

		return {
			"uri": uri,
			"mimeType": NOTE_MIME_TYPE,
			"text": content,
		}


	async def resource_list_notes(self):
		tenant = asab.contextvars.Tenant.get()

		notes_path = _normalize_path(self.NotesDirectory, '', tenant)
		if notes_path is None:
			raise ValueError("Path is not within the notes directory")

		resources = []
		for root, dirs, files in os.walk(notes_path):
			for file in files:
				if file.startswith('.'):
					continue

				if not file.endswith(NOTE_EXTENSION):
					continue

				path = root[len(notes_path):]
				if '/.' in path:
					continue

				# Handle root directory case (empty path)
				if path:
					uri = f"{NOTE_URI_PREFIX}/{path}/{file}"
					name = f"{path[1:]}/{file[:-len(NOTE_EXTENSION)]}"
				else:
					uri = f"{NOTE_URI_PREFIX}/{file}"
					name = file[:-len(NOTE_EXTENSION)]
				
				resources.append(asab.mcp.MCPToolResultResourceLink(
					uri=uri,
					name=name,
					description=f"Markdown note: {name}",
					mimeType=NOTE_MIME_TYPE,
				))

		return resources


def _normalize_path(base_path, user_path, tenant):
	'''
	Normalize the path to be within the base path.
	'''

	assert tenant is not None

	while user_path.startswith('/'):
		user_path = user_path[1:]

	abs_base = os.path.abspath(os.path.join(base_path, tenant))
	abs_user = os.path.abspath(os.path.join(abs_base, user_path))

	if os.path.commonpath([abs_base, abs_user]) == abs_base:
		return abs_user

	else:
		return None
