import os
import time
import asab.web.rest

from .utils import NOTE_EXTENSION

class MarkdownNotesWebHandler():

	def __init__(self, app, web):
		self.App = app
		web.add_get(r"/{tenant}/tree", self.get_tree)
		web.add_get(r"/{tenant}/note/{path:.*}", self.read_note)
		web.add_put(r"/{tenant}/note/{path:.*}", self.save_note)
		web.add_post(r"/{tenant}/note-create", self.create_note)
		web.add_post(r"/{tenant}/note-rename", self.rename_note)
		web.add_delete(r"/{tenant}/note/{path:.*}", self.delete_note)
		web.add_post(r"/{tenant}/directory-create", self.create_directory)
		web.add_post(r"/{tenant}/directory-rename", self.rename_directory)
		web.add_delete(r"/{tenant}/directory/{path:.*}", self.delete_directory)

	def _build_tree(self, directory_path, base_path=""):
		"""Recursively build the directory tree structure."""
		items = []

		try:
			entries = sorted(os.listdir(directory_path))
		except OSError:
			return items

		# First add directories
		for item in entries:
			if item.startswith('.'):
				continue

			item_path = os.path.join(directory_path, item)
			relative_path = f"{base_path}/{item}" if base_path else item

			if os.path.isdir(item_path):
				children = self._build_tree(item_path, relative_path)
				# Get the most recent mtime from children
				dir_mtime = 0
				for child in children:
					child_mtime = child.get("mtime", 0)
					if child_mtime > dir_mtime:
						dir_mtime = child_mtime
				
				items.append({
					"name": item,
					"path": relative_path,
					"type": "directory",
					"children": children,
					"mtime": dir_mtime,
				})

		# Then add notes
		for item in entries:
			if item.startswith('.'):
				continue

			item_path = os.path.join(directory_path, item)
			relative_path = f"{base_path}/{item}" if base_path else item

			if item.endswith(NOTE_EXTENSION) and os.path.isfile(item_path):
				# Get modification time
				try:
					mtime = os.path.getmtime(item_path)
				except OSError:
					mtime = 0
				
				items.append({
					"name": item,
					"path": relative_path,
					"type": "note",
					"mtime": mtime,
				})

		return items

	def get_tree(self, request):
		tenant = asab.contextvars.Tenant.get()

		root_path = self.App.normalize_note_path("", tenant)
		if root_path is None:
			raise asab.web.rest.HTTPNotFound()

		if not os.path.isdir(root_path):
			raise asab.web.rest.HTTPNotFound()

		tree = self._build_tree(root_path)

		data = {
			"result": "OK",
			"data": tree,
			"timestamp": time.time(),
		}

		return asab.web.rest.json_response(request, data)

	def read_note(self, request):
		tenant = asab.contextvars.Tenant.get()
		path = request.match_info.get("path", "")

		if not path.endswith(NOTE_EXTENSION):
			path += NOTE_EXTENSION

		note_path = self.App.normalize_note_path(path, tenant)
		if note_path is None:
			raise asab.web.rest.HTTPNotFound()

		if not os.path.isfile(note_path):
			raise asab.web.rest.HTTPNotFound()

		with open(note_path, "r") as f:
			content = f.read()

		# Get modification time
		try:
			mtime = os.path.getmtime(note_path)
		except OSError:
			mtime = 0

		data = {
			"result": "OK",
			"data": {
				"content": content,
				"path": path,
				"mtime": mtime,
			},
		}

		return asab.web.rest.json_response(request, data)

	async def save_note(self, request):
		tenant = asab.contextvars.Tenant.get()
		path = request.match_info.get("path", "")

		if not path.endswith(NOTE_EXTENSION):
			path += NOTE_EXTENSION

		note_path = self.App.normalize_note_path(path, tenant)
		if note_path is None:
			raise asab.web.rest.HTTPNotFound()

		# Ensure the note file exists (we don't create new notes via this endpoint)
		if not os.path.isfile(note_path):
			raise asab.web.rest.HTTPNotFound()

		# Parse the request body
		try:
			body = await request.json()
		except Exception:
			raise asab.web.rest.HTTPBadRequest()

		content = body.get("content")
		if content is None:
			raise asab.web.rest.HTTPBadRequest()

		# Write the content to the file
		with open(note_path, "w") as f:
			f.write(content)

		# Get new modification time
		try:
			mtime = os.path.getmtime(note_path)
		except OSError:
			mtime = 0

		data = {
			"result": "OK",
			"data": {
				"content": content,
				"path": path,
				"mtime": mtime,
			},
		}

		return asab.web.rest.json_response(request, data)


	async def create_note(self, request):
		tenant = asab.contextvars.Tenant.get()

		# Parse the request body
		try:
			body = await request.json()
		except Exception:
			body = {}

		# Get the directory where to create the note (empty string means root)
		directory = body.get("directory", "")
		# Get the name for the new note (required)
		name = body.get("name", "")

		if not name:
			raise asab.web.rest.HTTPBadRequest()

		# Sanitize name: remove path separators
		name = os.path.basename(name)

		# Validate and normalize the directory path
		if directory:
			dir_path = self.App.normalize_note_path(directory, tenant)
			if dir_path is None:
				raise asab.web.rest.HTTPNotFound()
			if not os.path.isdir(dir_path):
				raise asab.web.rest.HTTPNotFound()
		else:
			dir_path = self.App.normalize_note_path("", tenant)
			if dir_path is None:
				raise asab.web.rest.HTTPNotFound()

		# Ensure the filename has the proper extension
		if not name.endswith(NOTE_EXTENSION):
			filename = f"{name}{NOTE_EXTENSION}"
		else:
			filename = name

		note_path = os.path.join(dir_path, filename)

		# Check if file already exists
		if os.path.exists(note_path):
			raise asab.web.rest.HTTPConflict()

		# Create the new note file with empty content
		with open(note_path, "w") as f:
			f.write("")

		# Get modification time
		try:
			mtime = os.path.getmtime(note_path)
		except OSError:
			mtime = 0

		# Build the relative path for the response
		relative_path = f"{directory}/{filename}" if directory else filename

		data = {
			"result": "OK",
			"data": {
				"content": "",
				"path": relative_path,
				"mtime": mtime,
			},
		}

		return asab.web.rest.json_response(request, data)


	async def rename_note(self, request):
		tenant = asab.contextvars.Tenant.get()

		# Parse the request body
		try:
			body = await request.json()
		except Exception:
			raise asab.web.rest.HTTPBadRequest()

		old_path = body.get("old_path")
		new_name = body.get("new_name")

		if not old_path or not new_name:
			raise asab.web.rest.HTTPBadRequest()

		# Ensure old_path has the extension
		if not old_path.endswith(NOTE_EXTENSION):
			old_path += NOTE_EXTENSION

		# Validate old path
		old_note_path = self.App.normalize_note_path(old_path, tenant)
		if old_note_path is None:
			raise asab.web.rest.HTTPNotFound()

		if not os.path.isfile(old_note_path):
			raise asab.web.rest.HTTPNotFound()

		# Build the new path (same directory, new name)
		old_dir = os.path.dirname(old_path)

		# Sanitize new_name: remove path separators and ensure it has extension
		new_name = os.path.basename(new_name)  # Remove any path components
		if not new_name.endswith(NOTE_EXTENSION):
			new_name += NOTE_EXTENSION

		new_path = f"{old_dir}/{new_name}" if old_dir else new_name

		# Validate new path
		new_note_path = self.App.normalize_note_path(new_path, tenant)
		if new_note_path is None:
			raise asab.web.rest.HTTPBadRequest()

		# Check if new path already exists
		if os.path.exists(new_note_path):
			raise asab.web.rest.HTTPConflict()

		# Rename the file
		try:
			os.rename(old_note_path, new_note_path)
		except OSError:
			raise asab.web.rest.HTTPInternalServerError()

		# Get modification time
		try:
			mtime = os.path.getmtime(new_note_path)
		except OSError:
			mtime = 0

		data = {
			"result": "OK",
			"data": {
				"old_path": old_path,
				"path": new_path,
				"mtime": mtime,
			},
		}

		return asab.web.rest.json_response(request, data)


	async def delete_note(self, request):
		tenant = asab.contextvars.Tenant.get()
		path = request.match_info.get("path", "")

		if not path.endswith(NOTE_EXTENSION):
			path += NOTE_EXTENSION

		note_path = self.App.normalize_note_path(path, tenant)
		if note_path is None:
			raise asab.web.rest.HTTPNotFound()

		if not os.path.isfile(note_path):
			raise asab.web.rest.HTTPNotFound()

		# Delete the file
		try:
			os.remove(note_path)
		except OSError:
			raise asab.web.rest.HTTPInternalServerError()

		data = {
			"result": "OK",
			"data": {
				"path": path,
			},
		}

		return asab.web.rest.json_response(request, data)


	async def create_directory(self, request):
		tenant = asab.contextvars.Tenant.get()

		# Parse the request body
		try:
			body = await request.json()
		except Exception:
			body = {}

		# Get the parent directory where to create the new directory (empty string means root)
		parent_directory = body.get("parent_directory", "")
		# Get the name for the new directory (required)
		name = body.get("name", "")

		if not name:
			raise asab.web.rest.HTTPBadRequest()

		# Sanitize name: remove path separators
		dirname = os.path.basename(name)

		# Validate and normalize the parent directory path
		if parent_directory:
			parent_path = self.App.normalize_note_path(parent_directory, tenant)
			if parent_path is None:
				raise asab.web.rest.HTTPNotFound()
			if not os.path.isdir(parent_path):
				raise asab.web.rest.HTTPNotFound()
		else:
			parent_path = self.App.normalize_note_path("", tenant)
			if parent_path is None:
				raise asab.web.rest.HTTPNotFound()

		dir_path = os.path.join(parent_path, dirname)

		# Check if directory already exists
		if os.path.exists(dir_path):
			raise asab.web.rest.HTTPConflict()

		# Create the new directory
		try:
			os.makedirs(dir_path)
		except OSError:
			raise asab.web.rest.HTTPInternalServerError()

		# Build the relative path for the response
		relative_path = f"{parent_directory}/{dirname}" if parent_directory else dirname

		data = {
			"result": "OK",
			"data": {
				"path": relative_path,
				"name": dirname,
			},
		}

		return asab.web.rest.json_response(request, data)


	async def rename_directory(self, request):
		tenant = asab.contextvars.Tenant.get()

		# Parse the request body
		try:
			body = await request.json()
		except Exception:
			raise asab.web.rest.HTTPBadRequest()

		old_path = body.get("old_path")
		new_name = body.get("new_name")

		if not old_path or not new_name:
			raise asab.web.rest.HTTPBadRequest()

		# Validate old path
		old_dir_path = self.App.normalize_note_path(old_path, tenant)
		if old_dir_path is None:
			raise asab.web.rest.HTTPNotFound()

		if not os.path.isdir(old_dir_path):
			raise asab.web.rest.HTTPNotFound()

		# Build the new path (same parent directory, new name)
		parent_dir = os.path.dirname(old_path)

		# Sanitize new_name: remove path separators
		new_name = os.path.basename(new_name)

		new_path = f"{parent_dir}/{new_name}" if parent_dir else new_name

		# Validate new path
		new_dir_path = self.App.normalize_note_path(new_path, tenant)
		if new_dir_path is None:
			raise asab.web.rest.HTTPBadRequest()

		# Check if new path already exists
		if os.path.exists(new_dir_path):
			raise asab.web.rest.HTTPConflict()

		# Rename the directory
		try:
			os.rename(old_dir_path, new_dir_path)
		except OSError:
			raise asab.web.rest.HTTPInternalServerError()

		data = {
			"result": "OK",
			"data": {
				"old_path": old_path,
				"path": new_path,
			},
		}

		return asab.web.rest.json_response(request, data)


	async def delete_directory(self, request):
		import shutil

		tenant = asab.contextvars.Tenant.get()
		path = request.match_info.get("path", "")

		if not path:
			raise asab.web.rest.HTTPBadRequest()

		dir_path = self.App.normalize_note_path(path, tenant)
		if dir_path is None:
			raise asab.web.rest.HTTPNotFound()

		if not os.path.isdir(dir_path):
			raise asab.web.rest.HTTPNotFound()

		# Delete the directory and all its contents
		try:
			shutil.rmtree(dir_path)
		except OSError:
			raise asab.web.rest.HTTPInternalServerError()

		data = {
			"result": "OK",
			"data": {
				"path": path,
			},
		}

		return asab.web.rest.json_response(request, data)
