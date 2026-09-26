Do NOT commit real secrets.

Create these files locally before first stack deployment:
  secrets/camera_tts_api_key.txt
  secrets/cam_gate_password.txt

For additional cameras, create another secret file and declare it in stack.yml.
Example:
  secrets/cam_yard_password.txt
