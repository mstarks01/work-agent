Build and deploy pipeline for the in-store estate.

We run about 1,200 stores. Each store has a small server in the back office
running the till software as a container. This is how a change a developer
writes ends up on those servers.

Developers push their branches to our self-hosted git server, which sits on the
corporate network. A merge to `main` is what starts a build.

The build runner picks the merge up, fetches the source, resolves the
dependency lockfile and produces a container image. The runner lives in its own
build environment, separate from the corporate network. It pushes the finished
image to our image registry, which is also in the build environment. Images are
tagged with the commit sha.

When the image is pushed the runner calls the deploy controller and asks it to
make that image the current release. The deploy controller runs on the
corporate network and holds one record: which image sha the estate should be
on. The token the runner uses to call the controller is a shared build token
that is the same for every pipeline and has not been rotated since the pipeline
was set up.

The store servers do not get pushed to. Every store server asks the deploy
controller once a minute what the current release is, and if it differs from
what it is running it pulls that image from the registry and restarts the
container. The registry allows the pull; nobody has written down what a store
server presents to it, or what it presents to the controller.

The image registry is reachable from the store estate over the WAN. Traffic
between the stores and the corporate network goes over the retail WAN.

Two things worth saying that are not on the main path. Dependency resolution
reaches out to the public package registry on the internet, and the lockfile is
taken as given — the runner does not verify signatures on what it downloads.
And any developer can log in to the build runner and kick off a rebuild of
`main` by hand; that path does not require a merge and is not reviewed.

Nobody has documented whether the git server encrypts what it stores, or
whether the image registry does.
