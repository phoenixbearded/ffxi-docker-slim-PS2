FFXI Server: Docker Edition

Docker packaging for the Habex FFXI PS2 private server (Server App), with a built-in web UI protected by a password.

Build the image once, then run it anywhere Docker runs: a NAS, a home server, or a PC.

[!NOTE] The image is built on the machine that has your Habex files, then copied to the destination host. It is not pulled from a registry.

Contents
Requirements

1. Add the project files
2. Configure .env
3. Build the image
4. Deploy
5. Connecting from PCSX2 / PS2
   Screenshots
   Known issues
   Requirements
   Docker and Docker Compose on both the build machine and the destination host
   Your Habex folder, containing Disc/files/Game/Server/Server App
6. Add the project files

In the main Habex folder (next to Disc/files/Game/Server/Server App), add all of these files:

File Purpose
docker-compose.yml / docker-compose.nas.yml Compose file: use the regular one, or the NAS variant
Dockerfile Full image definition
Dockerfile.slim Slim image definition (used in the build below)
Dockerfile.dockerignore Files excluded from the build context
entrypoint.sh Container startup script
webui.py Web UI 2. Configure .env

Update the .env file with:

Web UI password: the login username is admin
Port: the port the web UI listens on 3. Build the image

Run these from the main Habex folder:

bash
docker build -f Dockerfile.slim -t ffxi-server:slim .
docker save ffxi-server:slim | gzip > ffxi-server-slim.tar.gz 4. Deploy

Copy ffxi-server-slim.tar.gz and your compose file to the destination (NAS, server, PC, etc.), then run this on that machine:

bash

# Load the image

sudo docker load < /volume1/docker/ffxi-server-slim.tar.gz

# Start the server

cd /path/to/docker/ffxi-server
sudo docker compose -f docker-compose.nas.yml up -d

[!TIP] Adjust /volume1/docker/ and /path/to/docker/ffxi-server to match where you copied the files. Use docker-compose.yml instead if you're not on a NAS.

5. Connecting from PCSX2 / PS2

PCSX2 and the PS2 have trouble with Docker network modes. When logging in, use the IP address of the machine hosting Docker.

Follow the same external/internal address rules as the original Habex project, with the same ports in the login portal.

Screenshots

<details> <summary>Click to expand</summary> <!-- TODO: replace each alt text with a short caption describing the screenshot --> <p align="center"> <img width="800" alt="Screenshot 1" src="https://github.com/user-attachments/assets/3062c392-69a2-4d7e-a50c-ae704f49ad46" /> <img width="800" alt="Screenshot 2" src="https://github.com/user-attachments/assets/778c6277-01d4-49d8-ad34-d673c2a1d741" /> <img width="800" alt="Screenshot 3" src="https://github.com/user-attachments/assets/142730a5-cde7-4839-b16a-0efb70522d31" /> <img width="800" alt="Screenshot 4" src="https://github.com/user-attachments/assets/d8cb24be-7a25-4425-8717-d40365138a56" /> <img width="800" alt="Screenshot 5" src="https://github.com/user-attachments/assets/4b0a01e3-8224-4496-b033-3a5ac5fc0c5f" /> <img width="800" alt="Screenshot 6" src="https://github.com/user-attachments/assets/71e5e576-0c0f-41d5-a79b-fe3745e78196" /> <img width="800" alt="Screenshot 7" src="https://github.com/user-attachments/assets/1b270685-c618-494f-b67a-45aa1a8ca25a" /> </p> </details>
Known issues
Login fails for an empty POL account (one with zero characters).
Workaround: back up your existing POL accounts and restore them into the Docker server.
Tracked as a bug (link the GitHub issue here once filed).
