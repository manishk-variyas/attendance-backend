# Redmine Project Management and Issue Tracking
#
# Redmine provides:
# - Issue tracking
# - Project management
# - Gantt charts, calendars, wikis
# - Document management
#
# Configuration is passed via environment variables in docker-compose.yml.

FROM redmine:6.1-alpine

# Install redmine_extended_api plugin (adds write access for Roles via /extended_api)
# Pinned to commit 2b5f34f (release 0.0.4) for reproducibility. No DB migrations required.
RUN git init /usr/src/redmine/plugins/redmine_extended_api \
    && cd /usr/src/redmine/plugins/redmine_extended_api \
    && git fetch --depth 1 https://github.com/jcatrysse/redmine_extended_api.git 2b5f34fe26e693f3d04b83e7f39881364c36e2d6 \
    && git checkout FETCH_HEAD \
    && rm -rf .git
