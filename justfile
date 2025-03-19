default:
    @just --list

[working-directory: 'openakun']
tailwind:
    npx @tailwindcss/cli -i ./static/tailwind-inp.css -o ./static/tailwind.css --watch

docker-run:
    docker compose -f docker_compose.dev.yml up

docker-build:
    docker compose -f docker_compose.dev.yml build

count-lines:
    tokei . --exclude node_modules/ --exclude openakun/static/vendor/ --exclude openakun/static/tailwind.css --exclude \*.json -s lines

test:
    pytest
