import json
import logging
import asyncio

from .datamodel import FunctionCall

L = logging.getLogger(__name__)

async def tool_ping(function_call: FunctionCall) -> None:
	"""
	Ping a target host or service to check if it's reachable.
	
	Args:
		target: The target host or service to ping
		reply: Async callback to submit JSON chunks to the webui client
	
	Returns:
		dict with the final result of the ping command
	"""
	yield "validating"

	try:
		arguments = json.loads(function_call.arguments)
	except Exception as e:
		L.exception("Exception occurred while parsing arguments: '{}'".format(function_call.arguments), struct_data={"error": str(e)})
		function_call.error = f"Exception occurred while parsing arguments."
		function_call.error = True
		return

	target = arguments.get("target")
	if not target:
		function_call.error = "Target is required"
		function_call.error = True
		return

	# Sanitize target to prevent command injection
	# Only allow alphanumeric, dots, hyphens, and colons (for IPv6)
	sanitized_target = "".join(
		c for c in target if c.isalnum() or c in ".-:"
	)
	
	if not sanitized_target:
		function_call.error = "Invalid target specified"
		function_call.error = True
		return
	
	cmd = ["ping", "-c", "4", sanitized_target]

	try:
		# Create subprocess for ping command
		# -c 4: send 4 packets
		process = await asyncio.create_subprocess_exec(
			*cmd,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE
		)
		
		return_code = 0
		pending = set([
			asyncio.create_task(process.stdout.readline(), name="stdout"),
			asyncio.create_task(process.stderr.readline(), name="stderr"),
			asyncio.create_task(process.wait(), name="return_code"),
		])
		while len(pending) > 0:
			done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
			for task in done:
				match task.get_name():
					
					case "stdout" | "stderr":
						data = task.result()
						if len(data) > 0:
							function_call.content += data.decode("utf-8", errors="replace")
							yield "progress"

							if task.get_name() == "stdout":
								pending.add(asyncio.create_task(process.stdout.readline(), name="stdout"))
							else:
								pending.add(asyncio.create_task(process.stderr.readline(), name="stderr"))
	
					case "return_code":
						return_code = task.result()

		if return_code != 0:
			function_call.content += "\nPing command failed with return code: " + str(return_code)	
			function_call.error = True

		yield "completed"

	except FileNotFoundError:
		L.warning("ping command not found on this system")
		function_call.error = "ping command not found on this system"
		function_call.error = True
		
	except Exception as e:
		L.exception("Exception occurred while executing ping", struct_data={"error": str(e)})
		function_call.error = f"Exception occurred while executing ping"
		function_call.error = True
