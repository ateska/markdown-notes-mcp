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
