import os
import time
import json
import asyncio

import aiohttp_sse
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

		web.add_get(r"/{tenant}/directory/{path:.*}", self.list_directory)
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


	async def get_tree(self, request):
		tenant = asab.contextvars.Tenant.get()

		root_path = self.App.normalize_note_path("", tenant)
		if root_path is None:
			raise KeyError("Path not found")

		if not os.path.isdir(root_path):
			raise KeyError("Path not found")

		if request.headers.get("accept") == "text/event-stream":
			# SSE version of the response
			async with aiohttp_sse.sse_response(request) as response:
				while True:
					tree = self._build_tree(root_path)
					await response.send(data=json.dumps(tree), event="tree")
					await asyncio.sleep(5)

				return response

		tree = self._build_tree(root_path)

		data = {
			"result": "OK",
			"data": tree,
			"timestamp": time.time(),
		}

		return asab.web.rest.json_response(request, data)


	async def list_directory(self, request):
		tenant = asab.contextvars.Tenant.get()
		path = request.match_info.get("path", "")
		directories = str(request.query.get("directories", "false")).lower() in ["true", "1", "t", "y", "yes", ""]

		directory_path = self.App.normalize_note_path(path, tenant)
		if directory_path is None:
			return asab.web.rest.json_response(request, {"result": "NOT-FOUND", "error": "Path is not within the notes directory"}, status=404)

		if not os.path.isdir(directory_path):
			return asab.web.rest.json_response(request, {"result": "NOT-FOUND", "error": f"Directory '{path}' does not exist. Use an empty string to list the root directory."}, status=404)

		result = {
		}

		result["notes"] = list(note for note in os.listdir(directory_path) if note.endswith(NOTE_EXTENSION) and not note.startswith('.'))

		if directories:
			result["directories"] = list(dir for dir in os.listdir(directory_path) if os.path.isdir(os.path.join(directory_path, dir)) and not dir.startswith('.'))

		data = {
			"result": "OK",
			"data": result,
		}
		return asab.web.rest.json_response(request, data)


	async def read_note(self, request):
		tenant = asab.contextvars.Tenant.get()
		path = request.match_info.get("path", "")

		if not path.endswith(NOTE_EXTENSION):
			path += NOTE_EXTENSION

		note_path = self.App.normalize_note_path(path, tenant)
		if note_path is None:
			return asab.web.rest.json_response(request, {"result": "NOT-FOUND"}, status=404)

		if not os.path.isfile(note_path):
			return asab.web.rest.json_response(request, {"result": "NOT-FOUND"}, status=404)

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
			return asab.web.rest.json_response(request, {"result": "ERROR", "error": "Note path is incorrect."}, status=400)
		# Parse the request body
		try:
			body = await request.json()
		except Exception:
			return asab.web.rest.json_response(request, {"result": "ERROR", "error": "Body is not a valid JSON object."}, status=400)

		content = body.get("content")
		if content is None:
			return asab.web.rest.json_response(request, {"result": "ERROR", "error": "Content is not provided."}, status=400)

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
			raise ValueError("Invalid request body")

		# Sanitize name: remove path separators
		name = os.path.basename(name)

		# Validate and normalize the directory path
		if directory:
			dir_path = self.App.normalize_note_path(directory, tenant)
			if dir_path is None:
				raise KeyError("Directory not found")
			if not os.path.isdir(dir_path):
				raise KeyError("Directory not found")
		else:
			dir_path = self.App.normalize_note_path("", tenant)
			if dir_path is None:
				raise KeyError("Directory not found")

		# Ensure the filename has the proper extension
		if not name.endswith(NOTE_EXTENSION):
			filename = f"{name}{NOTE_EXTENSION}"
		else:
			filename = name

		note_path = os.path.join(dir_path, filename)

		# Check if file already exists
		if os.path.exists(note_path):
			raise ValueError("Note already exists")

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
			raise ValueError("Invalid request body")

		old_path = body.get("old_path")
		new_name = body.get("new_name")

		if not old_path or not new_name:
			raise ValueError("Invalid request body")

		# Ensure old_path has the extension
		if not old_path.endswith(NOTE_EXTENSION):
			old_path += NOTE_EXTENSION

		# Validate old path
		old_note_path = self.App.normalize_note_path(old_path, tenant)
		if old_note_path is None:
			raise KeyError("Note not found")

		if not os.path.isfile(old_note_path):
			raise KeyError("Note not found")

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
			raise ValueError("Invalid request body")

		# Check if new path already exists
		if os.path.exists(new_note_path):
			raise ValueError("Note already exists")

		# Rename the file
		try:
			os.rename(old_note_path, new_note_path)
		except OSError:
			raise ValueError("Failed to rename note")

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
			raise KeyError("Note not found")

		if not os.path.isfile(note_path):
			raise KeyError("Note not found")

		# Delete the file
		try:
			os.remove(note_path)
		except OSError:
			raise ValueError("Failed to delete note")

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
			raise ValueError("Invalid request body")

		# Sanitize name: remove path separators
		dirname = os.path.basename(name)

		# Validate and normalize the parent directory path
		if parent_directory:
			parent_path = self.App.normalize_note_path(parent_directory, tenant)
			if parent_path is None:
				raise KeyError("Directory not found")
			if not os.path.isdir(parent_path):
				raise KeyError("Directory not found")
		else:
			parent_path = self.App.normalize_note_path("", tenant)
			if parent_path is None:
				raise KeyError("Directory not found")

		dir_path = os.path.join(parent_path, dirname)

		# Check if directory already exists
		if os.path.exists(dir_path):
			raise ValueError("Directory already exists")

		# Create the new directory
		try:
			os.makedirs(dir_path)
		except OSError:
			raise ValueError("Failed to create directory")

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
			raise ValueError("Invalid request body")

		old_path = body.get("old_path")
		new_name = body.get("new_name")

		if not old_path or not new_name:
			raise ValueError("Invalid request body")

		# Validate old path
		old_dir_path = self.App.normalize_note_path(old_path, tenant)
		if old_dir_path is None:
			raise KeyError("Directory not found")

		if not os.path.isdir(old_dir_path):
			raise KeyError("Directory not found")

		# Build the new path (same parent directory, new name)
		parent_dir = os.path.dirname(old_path)

		# Sanitize new_name: remove path separators
		new_name = os.path.basename(new_name)

		new_path = f"{parent_dir}/{new_name}" if parent_dir else new_name

		# Validate new path
		new_dir_path = self.App.normalize_note_path(new_path, tenant)
		if new_dir_path is None:
			raise ValueError("Invalid request body")

		# Check if new path already exists
		if os.path.exists(new_dir_path):
			raise ValueError("Directory already exists")

		# Rename the directory
		try:
			os.rename(old_dir_path, new_dir_path)
		except OSError:
			raise ValueError("Failed to rename directory")

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
			raise ValueError("Invalid request body")

		dir_path = self.App.normalize_note_path(path, tenant)
		if dir_path is None:
			raise KeyError("Directory not found")

		if not os.path.isdir(dir_path):
			raise KeyError("Directory not found")

		# Delete the directory and all its contents
		try:
			shutil.rmtree(dir_path)
		except OSError:
			raise ValueError("Failed to delete directory")

		data = {
			"result": "OK",
			"data": {
				"path": path,
			},
		}

		return asab.web.rest.json_response(request, data)
