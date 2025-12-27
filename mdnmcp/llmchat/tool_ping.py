import logging
import asyncio

from .chat import ChatToolResult

L = logging.getLogger(__name__)

async def tool_ping(call_id: str, arguments: dict, reply) -> None:
	"""
	Ping a target host or service to check if it's reachable.
	
	Args:
		target: The target host or service to ping
		reply: Async callback to submit JSON chunks to the webui client
	
	Returns:
		dict with the final result of the ping command
	"""
	target = arguments.get("target")
	if not target:
		error_msg = "Target is required"
		await reply({
			"type": "tool.error",
			"call_id": call_id,
			"data": {"error": error_msg}
		})
		return

	# Sanitize target to prevent command injection
	# Only allow alphanumeric, dots, hyphens, and colons (for IPv6)
	sanitized_target = "".join(
		c for c in target if c.isalnum() or c in ".-:"
	)
	
	if not sanitized_target:
		error_msg = "Invalid target specified"
		await reply({
			"type": "tool.error",
			"call_id": call_id,
			"data": {"error": error_msg}
		})
		return
	
	# Notify that ping is starting
	await reply({
		"type": "tool.started",
		"call_id": call_id,
		"data": {"target": sanitized_target}
	})
	
	cmd = ["ping", "-c", "4", sanitized_target]

	try:
		# Create subprocess for ping command
		# -c 4: send 4 packets
		process = await asyncio.create_subprocess_exec(
			*cmd,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE
		)
		
		output = " ".join(cmd) + "\n"

		async def read_stream(stream, stream_type: str):
			"""Read from stream and send chunks to reply callback."""
			result = ""
			while True:
				# Read line by line for better real-time output
				line = await stream.readline()
				if not line:
					break
				
				chunk = line.decode("utf-8", errors="replace")
				result += chunk
				await reply({
					"type": f"tool.{stream_type}",
					"call_id": call_id,
					"data": {"chunk": chunk}
				})

			return result
		
		# Read stdout and stderr concurrently
		stdout, stderr = await asyncio.gather(
			read_stream(process.stdout, "stdout"),
			read_stream(process.stderr, "stderr")
		)
		output += stdout
		output += stderr

		# Wait for the process to complete
		return_code = await process.wait()

		output += "Command completed with return code: " + str(return_code)

		# Send completion message
		result = {
			"return_code": return_code,
			"target": sanitized_target
		}
		
		await reply({
			"type": "tool.completed",
			"call_id": call_id,
			"data": result
		})

		return ChatToolResult(
			output=output,
			call_id=call_id,
		)
				
	except FileNotFoundError:
		error_msg = "ping command not found on this system"
		await reply({
			"type": "tool.error",
			"call_id": call_id,
			"data": {"error": error_msg}
		})
		return
		
	except Exception as e:
		L.exception("Error executing ping", struct_data={"error": str(e)})
		error_msg = f"Error executing ping: {str(e)}"
		await reply({
			"type": "tool.error",
			"call_id": call_id,
			"data": {"error": error_msg}
		})
		return
