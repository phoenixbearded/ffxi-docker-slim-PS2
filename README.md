To get the server up and running, you have to compile the image first before uploading:

# I - File Setup

# In the main Habex folder (alongside Disc/files/Game/Server/Server App):

# add all these project files:

- docker compose (regular or NAS)
- Dockerfile
- Dockerfile.dockerignore
- Dockerfile.slim
- entrypoint.sh
- webui.py

# I.5 - update .env file with webUI password (username is admin) and Port

# II - Docker img build

# run the following in main folder

docker build -f Dockerfile.slim -t ffxi-server:slim .
docker save ffxi-server:slim | gzip > ffxi-server-slim.tar.gz

# III - Bring the image up

# copy to destination (NAS,server,PC,etc), then on there:

sudo docker load < /volume1/docker/ffxi-server-slim.tar.gz
cd /path/to/docker/ffxi-server && sudo docker compose -f docker-compose.nas.yml up -d

# IV - good to know:

# Since the PCSX2 or PS2 have trouble with network modes for docker, the IP address is the one where docker is hosted when logging in, follow the same external or internal address rules that Habex project had going on, with same ports in login portal

# Known issues

# Cannot login with empty POL account with zero characters

# As of now, need to do a backup from POL accounts and restore over into the docker server in order for it to work. I will log it as a bug.

<img width="920" height="267" alt="Screenshot_20261020_154324" src="https://github.com/user-attachments/assets/1b270685-c618-494f-b67a-45aa1a8ca25a" />
<img width="2311" height="906" alt="Screenshot_20261020_154151" src="https://github.com/user-attachments/assets/71e5e576-0c0f-41d5-a79b-fe3745e78196" />
<img width="2307" height="890" alt="Screenshot_20261020_154131" src="https://github.com/user-attachments/assets/4b0a01e3-8224-4496-b033-3a5ac5fc0c5f" />
<img width="2307" height="890" alt="Screenshot_20261020_154057" src="https://github.com/user-attachments/assets/d8cb24be-7a25-4425-8717-d40365138a56" />
<img width="2307" height="890" alt="Screenshot_20261020_154025" src="https://github.com/user-attachments/assets/142730a5-cde7-4839-b16a-0efb70522d31" />
<img width="2307" height="890" alt="Screenshot_20261020_153933" src="https://github.com/user-attachments/assets/778c6277-01d4-49d8-ad34-d673c2a1d741" />
<img width="2229" height="831" alt="Screenshot_20261020_153828" src="https://github.com/user-attachments/assets/3062c392-69a2-4d7e-a50c-ae704f49ad46" />
