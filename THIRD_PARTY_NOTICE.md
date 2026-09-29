# Third-party notices

## HCNetSDK

This project directory contains HCNetSDK binary libraries supplied by the project owner so the runtime image can be self-contained.

Before pushing this repository or publishing a container image publicly, verify that your HCNetSDK/EZVIZ/Hikvision SDK license permits redistribution. A private GitHub repository and private GHCR package are the safer default when redistribution rights are unclear.

The bundled SDK in this project is x86-64 Linux, so the Docker image is intentionally built only for `linux/amd64`.

## imou-homeassistant reference implementation

Portions of the Imou/Dahua local talk and intercom design were adapted from the user-supplied `imou-homeassistant` project.

MIT License — Copyright (c) 2026 TriTue2011. The original MIT license text is preserved in the supplied reference archive; the attribution and permission notice must be retained when substantial portions are redistributed.
