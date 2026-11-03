ARG ALPINE_IMAGE=alpine:3.21
FROM ${ALPINE_IMAGE}

# Always build this image from the repository root. Keeping the source path
# explicit prevents an accidental nested /seed/skills/skills layout.
COPY skills/ /seed/skills/

RUN test -f /seed/skills/public/ppt-master/SKILL.md \
    && test ! -d /seed/skills/skills
