# Third-party SDK notice

This project directory contains HCNetSDK binary libraries supplied by the project owner so the runtime image can be self-contained.

Before pushing this repository or publishing a container image publicly, verify that your HCNetSDK/EZVIZ/Hikvision SDK license permits redistribution. A private GitHub repository and private GHCR package are the safer default when redistribution rights are unclear.

The bundled SDK in this project is x86-64 Linux, so the Docker image is intentionally built only for `linux/amd64`.
