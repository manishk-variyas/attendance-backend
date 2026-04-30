# Nginx Reverse Proxy Docker Image
#
# This creates a lightweight Nginx container with our custom configuration.
# The nginx:alpine image is ~20MB (vs ~150MB for the full image).
#
# What it does:
# - Uses Alpine Linux based Nginx
# - Copies our custom proxy config from conf.d/ to Nginx's config directory
# - The config forwards all requests to the backend service

FROM nginx:alpine

# Copy custom Nginx configuration
# This overwrites the default Nginx config with our reverse proxy setup
COPY conf.d/ /etc/nginx/conf.d/
