Podcast hosting — what we run, written up by the platform team.

Podcasters publish their shows with us. A podcaster uploads each episode's audio file through the upload API, which is on the internet. Each show gets 50 GB of storage in total, and the upload API refuses an upload that would take a show over its 50 GB. The upload API stores the audio in the media store and the episode details in the show database. Podcasters sign in with an email address and a password.

The transcoder picks up each new audio file from the media store and makes the smaller versions that listeners' apps download. Each transcoder runs at most two jobs at once.

Listeners' podcast apps fetch each show's RSS feed from the feed service, which is public. The feed service reads the show database to build each feed. We do not rate limit the feed service, because apps poll feeds all day and we never want an app to miss a new episode.

Every night our ad partner sends us files of download counts. The stats importer starts a new import job for every file that arrives, with no cap on how many run together, and writes the counts to the stats database.

Leo says the upload API probably also has a size limit on a single file, but he would have to check.

All of this runs in one hosting account. The podcasters, the listeners' apps and the ad partner are outside it.
