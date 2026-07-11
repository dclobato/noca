#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

variable "REPO" {
  default = "ghcr.io/dclobato/noca"
}

variable "VERSION" {
  default = ""
}

variable "PLATFORMS" {
  default = "linux/amd64,linux/arm64"
}

variable "NAME_SEPARATOR" {
  default = "/"
}

variable "ALT_REPO" {
  default = ""
}

variable "ALT_NAME_SEPARATOR" {
  default = "/"
}

variable "BAKE_NO_CACHE" {
  default = false
}

variable "JUDGE_ISOLATE_TAG" {
  default = "v2.6"
}

group "release" {
  targets = [
    "webapp",
    "arena",
    "autojudge",
    "rating",
    "aiassistant",
    "judge-bash-compile",
    "judge-bash-run",
    "judge-c-sharp-compile",
    "judge-c-sharp-run",
    "judge-fortran-compile",
    "judge-fortran-run",
    "judge-fpc-pascal-compile",
    "judge-fpc-pascal-run",
    "judge-gcc-c17-compile",
    "judge-gcc-c17-run",
    "judge-gcc-cpp23-compile",
    "judge-gcc-cpp23-run",
    "judge-go-compile",
    "judge-go-run",
    "judge-haskell-compile",
    "judge-haskell-run",
    "judge-java-compile",
    "judge-java-run",
    "judge-javascript-compile",
    "judge-javascript-run",
    "judge-kotlin-compile",
    "judge-kotlin-run",
    "judge-lua-compile",
    "judge-lua-run",
    "judge-perl-compile",
    "judge-perl-run",
    "judge-prolog-compile",
    "judge-prolog-run",
    "judge-python3-compile",
    "judge-python3-run",
    "judge-ruby-compile",
    "judge-ruby-run",
    "judge-rust-compile",
    "judge-rust-run",
    "judge-swift-compile",
    "judge-swift-run",
  ]
}

target "_publish-common" {
  platforms = split(",", PLATFORMS)
  no-cache = BAKE_NO_CACHE
}

target "_app-consumer" {
  inherits = ["_publish-common"]
  context = "."
  contexts = {
    "app-base" = "target:app-base"
  }
  args = {
    APP_BASE_REF = "app-base"
  }
}

target "_assets-consumer" {
  contexts = {
    "assets-base" = "target:assets-base"
  }
  args = {
    ASSETS_BASE_REF = "assets-base"
  }
}

target "_run-consumer" {
  inherits = ["_publish-common"]
  contexts = {
    "isolate-base" = "target:isolate-base"
  }
  args = {
    ISOLATE_BASE_REF = "isolate-base"
  }
}

target "_native-compile-consumer" {
  inherits = ["_publish-common"]
  contexts = {
    "judge-compile-base" = "target:judge-compile-base"
  }
  args = {
    JUDGE_COMPILE_BASE_REF = "judge-compile-base"
  }
}

target "_plain-compile-consumer" {
  inherits = ["_publish-common"]
}

target "app-base" {
  inherits = ["_publish-common"]
  context = "."
  dockerfile = "containers/app-base/Dockerfile"
  output = ["type=cacheonly"]
}

target "assets-base" {
  inherits = ["_publish-common"]
  platforms = ["linux/amd64"]
  context = "."
  dockerfile = "containers/assets-base/Dockerfile"
  contexts = {
    "app-base" = "target:app-base"
  }
  args = {
    APP_BASE_REF = "app-base"
  }
  output = ["type=cacheonly"]
}

target "isolate-base" {
  inherits = ["_publish-common"]
  context = "containers/isolate-base"
  args = {
    JUDGE_ISOLATE_TAG = "${JUDGE_ISOLATE_TAG}"
  }
  output = ["type=cacheonly"]
}

target "judge-compile-base" {
  inherits = ["_publish-common"]
  context = "containers/judge-compile-base"
  output = ["type=cacheonly"]
}

target "webapp" {
  inherits = ["_app-consumer", "_assets-consumer"]
  dockerfile = "containers/webapp/Dockerfile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}webapp",
      "${REPO}${NAME_SEPARATOR}webapp:${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}webapp"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}webapp",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}webapp:${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}webapp"]
    ) : [],
  )
}

target "arena" {
  inherits = ["_app-consumer", "_assets-consumer"]
  dockerfile = "containers/arena/Dockerfile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}arena",
      "${REPO}${NAME_SEPARATOR}arena:${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}arena"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}arena",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}arena:${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}arena"]
    ) : [],
  )
}

target "autojudge" {
  inherits = ["_app-consumer"]
  dockerfile = "containers/autojudge/Dockerfile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}autojudge",
      "${REPO}${NAME_SEPARATOR}autojudge:${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}autojudge"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}autojudge",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}autojudge:${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}autojudge"]
    ) : [],
  )
}

target "rating" {
  inherits = ["_app-consumer"]
  dockerfile = "containers/rating/Dockerfile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}rating",
      "${REPO}${NAME_SEPARATOR}rating:${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}rating"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}rating",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}rating:${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}rating"]
    ) : [],
  )
}

target "aiassistant" {
  inherits = ["_app-consumer"]
  dockerfile = "containers/aiassistant/Dockerfile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}aiassistant",
      "${REPO}${NAME_SEPARATOR}aiassistant:${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}aiassistant"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}aiassistant",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}aiassistant:${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}aiassistant"]
    ) : [],
  )
}

target "judge-bash-compile" {
  inherits = ["_plain-compile-consumer"]
  context = "containers/languages/bash/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-bash:compile",
      "${REPO}${NAME_SEPARATOR}judge-bash:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-bash:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-bash:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-bash:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-bash:compile"]
    ) : [],
  )
}

target "judge-bash-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/bash/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-bash:run",
      "${REPO}${NAME_SEPARATOR}judge-bash:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-bash:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-bash:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-bash:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-bash:run"]
    ) : [],
  )
}

target "judge-c-sharp-compile" {
  inherits = ["_plain-compile-consumer"]
  context = "containers/languages/c-sharp/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-c-sharp:compile",
      "${REPO}${NAME_SEPARATOR}judge-c-sharp:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-c-sharp:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-c-sharp:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-c-sharp:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-c-sharp:compile"]
    ) : [],
  )
}

target "judge-c-sharp-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/c-sharp/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-c-sharp:run",
      "${REPO}${NAME_SEPARATOR}judge-c-sharp:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-c-sharp:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-c-sharp:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-c-sharp:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-c-sharp:run"]
    ) : [],
  )
}

target "judge-fortran-compile" {
  inherits = ["_native-compile-consumer"]
  context = "containers/languages/fortran/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-fortran:compile",
      "${REPO}${NAME_SEPARATOR}judge-fortran:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-fortran:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-fortran:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-fortran:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-fortran:compile"]
    ) : [],
  )
}

target "judge-fortran-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/fortran/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-fortran:run",
      "${REPO}${NAME_SEPARATOR}judge-fortran:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-fortran:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-fortran:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-fortran:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-fortran:run"]
    ) : [],
  )
}

target "judge-fpc-pascal-compile" {
  inherits = ["_native-compile-consumer"]
  context = "containers/languages/fpc-pascal/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-fpc-pascal:compile",
      "${REPO}${NAME_SEPARATOR}judge-fpc-pascal:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-fpc-pascal:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-fpc-pascal:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-fpc-pascal:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-fpc-pascal:compile"]
    ) : [],
  )
}

target "judge-fpc-pascal-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/fpc-pascal/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-fpc-pascal:run",
      "${REPO}${NAME_SEPARATOR}judge-fpc-pascal:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-fpc-pascal:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-fpc-pascal:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-fpc-pascal:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-fpc-pascal:run"]
    ) : [],
  )
}

target "judge-gcc-c17-compile" {
  inherits = ["_native-compile-consumer"]
  context = "containers/languages/gcc-c17/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-gcc-c17:compile",
      "${REPO}${NAME_SEPARATOR}judge-gcc-c17:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-gcc-c17:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-gcc-c17:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-gcc-c17:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-gcc-c17:compile"]
    ) : [],
  )
}

target "judge-gcc-c17-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/gcc-c17/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-gcc-c17:run",
      "${REPO}${NAME_SEPARATOR}judge-gcc-c17:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-gcc-c17:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-gcc-c17:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-gcc-c17:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-gcc-c17:run"]
    ) : [],
  )
}

target "judge-gcc-cpp23-compile" {
  inherits = ["_native-compile-consumer"]
  context = "containers/languages/gcc-cpp23/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-gcc-cpp23:compile",
      "${REPO}${NAME_SEPARATOR}judge-gcc-cpp23:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-gcc-cpp23:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-gcc-cpp23:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-gcc-cpp23:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-gcc-cpp23:compile"]
    ) : [],
  )
}

target "judge-gcc-cpp23-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/gcc-cpp23/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-gcc-cpp23:run",
      "${REPO}${NAME_SEPARATOR}judge-gcc-cpp23:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-gcc-cpp23:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-gcc-cpp23:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-gcc-cpp23:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-gcc-cpp23:run"]
    ) : [],
  )
}

target "judge-go-compile" {
  inherits = ["_plain-compile-consumer"]
  context = "containers/languages/go/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-go:compile",
      "${REPO}${NAME_SEPARATOR}judge-go:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-go:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-go:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-go:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-go:compile"]
    ) : [],
  )
}

target "judge-go-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/go/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-go:run",
      "${REPO}${NAME_SEPARATOR}judge-go:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-go:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-go:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-go:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-go:run"]
    ) : [],
  )
}

target "judge-haskell-compile" {
  inherits = ["_native-compile-consumer"]
  context = "containers/languages/haskell/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-haskell:compile",
      "${REPO}${NAME_SEPARATOR}judge-haskell:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-haskell:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-haskell:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-haskell:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-haskell:compile"]
    ) : [],
  )
}

target "judge-haskell-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/haskell/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-haskell:run",
      "${REPO}${NAME_SEPARATOR}judge-haskell:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-haskell:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-haskell:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-haskell:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-haskell:run"]
    ) : [],
  )
}

target "judge-java-compile" {
  inherits = ["_plain-compile-consumer"]
  context = "containers/languages/java/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-java:compile",
      "${REPO}${NAME_SEPARATOR}judge-java:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-java:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-java:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-java:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-java:compile"]
    ) : [],
  )
}

target "judge-java-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/java/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-java:run",
      "${REPO}${NAME_SEPARATOR}judge-java:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-java:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-java:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-java:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-java:run"]
    ) : [],
  )
}

target "judge-javascript-compile" {
  inherits = ["_plain-compile-consumer"]
  context = "containers/languages/javascript/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-javascript:compile",
      "${REPO}${NAME_SEPARATOR}judge-javascript:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-javascript:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-javascript:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-javascript:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-javascript:compile"]
    ) : [],
  )
}

target "judge-javascript-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/javascript/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-javascript:run",
      "${REPO}${NAME_SEPARATOR}judge-javascript:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-javascript:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-javascript:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-javascript:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-javascript:run"]
    ) : [],
  )
}

target "judge-kotlin-compile" {
  inherits = ["_plain-compile-consumer"]
  context = "containers/languages/kotlin/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-kotlin:compile",
      "${REPO}${NAME_SEPARATOR}judge-kotlin:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-kotlin:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-kotlin:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-kotlin:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-kotlin:compile"]
    ) : [],
  )
}

target "judge-kotlin-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/kotlin/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-kotlin:run",
      "${REPO}${NAME_SEPARATOR}judge-kotlin:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-kotlin:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-kotlin:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-kotlin:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-kotlin:run"]
    ) : [],
  )
}

target "judge-lua-compile" {
  inherits = ["_native-compile-consumer"]
  context = "containers/languages/lua/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-lua:compile",
      "${REPO}${NAME_SEPARATOR}judge-lua:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-lua:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-lua:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-lua:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-lua:compile"]
    ) : [],
  )
}

target "judge-lua-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/lua/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-lua:run",
      "${REPO}${NAME_SEPARATOR}judge-lua:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-lua:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-lua:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-lua:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-lua:run"]
    ) : [],
  )
}

target "judge-prolog-compile" {
  inherits = ["_native-compile-consumer"]
  context = "containers/languages/prolog/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-prolog:compile",
      "${REPO}${NAME_SEPARATOR}judge-prolog:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-prolog:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-prolog:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-prolog:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-prolog:compile"]
    ) : [],
  )
}

target "judge-prolog-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/prolog/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-prolog:run",
      "${REPO}${NAME_SEPARATOR}judge-prolog:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-prolog:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-prolog:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-prolog:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-prolog:run"]
    ) : [],
  )
}

target "judge-python3-compile" {
  inherits = ["_plain-compile-consumer"]
  context = "containers/languages/python3/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-python3:compile",
      "${REPO}${NAME_SEPARATOR}judge-python3:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-python3:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-python3:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-python3:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-python3:compile"]
    ) : [],
  )
}

target "judge-python3-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/python3/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-python3:run",
      "${REPO}${NAME_SEPARATOR}judge-python3:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-python3:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-python3:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-python3:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-python3:run"]
    ) : [],
  )
}

target "judge-ruby-compile" {
  inherits = ["_plain-compile-consumer"]
  context = "containers/languages/ruby/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-ruby:compile",
      "${REPO}${NAME_SEPARATOR}judge-ruby:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-ruby:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-ruby:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-ruby:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-ruby:compile"]
    ) : [],
  )
}

target "judge-ruby-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/ruby/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-ruby:run",
      "${REPO}${NAME_SEPARATOR}judge-ruby:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-ruby:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-ruby:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-ruby:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-ruby:run"]
    ) : [],
  )
}

target "judge-rust-compile" {
  inherits = ["_plain-compile-consumer"]
  context = "containers/languages/rust/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-rust:compile",
      "${REPO}${NAME_SEPARATOR}judge-rust:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-rust:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-rust:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-rust:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-rust:compile"]
    ) : [],
  )
}

target "judge-rust-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/rust/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-rust:run",
      "${REPO}${NAME_SEPARATOR}judge-rust:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-rust:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-rust:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-rust:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-rust:run"]
    ) : [],
  )
}

target "judge-swift-compile" {
  inherits = ["_plain-compile-consumer"]
  context = "containers/languages/swift/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-swift:compile",
      "${REPO}${NAME_SEPARATOR}judge-swift:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-swift:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-swift:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-swift:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-swift:compile"]
    ) : [],
  )
}

target "judge-swift-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/swift/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-swift:run",
      "${REPO}${NAME_SEPARATOR}judge-swift:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-swift:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-swift:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-swift:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-swift:run"]
    ) : [],
  )
}

target "judge-perl-compile" {
  inherits = ["_plain-compile-consumer"]
  context = "containers/languages/perl/compile"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-perl:compile",
      "${REPO}${NAME_SEPARATOR}judge-perl:compile-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-perl:compile"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-perl:compile",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-perl:compile-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-perl:compile"]
    ) : [],
  )
}

target "judge-perl-run" {
  inherits = ["_run-consumer"]
  context = "containers/languages/perl/run"
  tags = concat(
    VERSION != "" ? [
      "${REPO}${NAME_SEPARATOR}judge-perl:run",
      "${REPO}${NAME_SEPARATOR}judge-perl:run-${VERSION}",
    ] : ["${REPO}${NAME_SEPARATOR}judge-perl:run"],
    ALT_REPO != "" ? (
      VERSION != "" ? [
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-perl:run",
        "${ALT_REPO}${ALT_NAME_SEPARATOR}judge-perl:run-${VERSION}",
      ] : ["${ALT_REPO}${ALT_NAME_SEPARATOR}judge-perl:run"]
    ) : [],
  )
}
