#!/bin/bash -eu
# SPDX-License-Identifier: Apache-2.0
#
# ClusterFuzzLite build: compile every fuzz target in arms/fuzz with the sanitizer the
# run asks for, and put each one in $OUT with its seed inputs. The base image sets $SRC,
# $OUT and the sanitizer flags. Locally, "Fuzzing" in CONTRIBUTING.md runs the same
# targets with cargo-fuzz.

cd "$SRC/octo-pod/arms/fuzz"

# Fuzz the dependency versions the arms ship with: start from the workspace lock file.
cp ../Cargo.lock Cargo.lock

cargo fuzz build -O --debug-assertions

host="$(rustc -vV | sed -n 's/^host: //p')"
release_dir="target/$host/release"
vectors=../sapient-receiver/tests/vectors

for target in $(cargo fuzz list); do
  cp "$release_dir/$target" "$OUT/"
  # libFuzzer starts each target from $OUT/<target>_seed_corpus.zip.
  if [ -d "seeds/$target" ]; then
    zip -j -q "$OUT/${target}_seed_corpus.zip" "seeds/$target"/*
  fi
done

# The JSON target starts from the SAPIENT reference vectors themselves.
zip -j -q "$OUT/sapient_json_seed_corpus.zip" "$vectors"/true/*.json "$vectors"/false/*.json
