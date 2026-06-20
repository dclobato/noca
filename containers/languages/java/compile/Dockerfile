# noca/judge-java:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned: eclipse-temurin:21.0.10+7-jdk (Ubuntu 24.04) @ sha256:3b0a98dfbdf1067c20a7854cec159551777d2ee1381bc76cd4bd0719f543b148

FROM eclipse-temurin:21-jdk@sha256:3b0a98dfbdf1067c20a7854cec159551777d2ee1381bc76cd4bd0719f543b148

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox owned by judge so put_archive writes readable files.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
