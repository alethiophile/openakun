#!/bin/bash

# This script creates subresource integrity hashes for files, by
# piping shasum output through base64.

dgst=$(shasum -b -a 384 $1 | xxd -r -p | base64)
echo "sha384-$dgst"
