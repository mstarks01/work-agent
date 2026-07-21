Content site — CMS, quick description.

Public site: readers hit the web server over HTTPS, some of them signed in.
The web server runs the CMS and keeps pages, accounts and comments in a MySQL
database on the same hosted network.

Images, stylesheets and downloads do not come off the web server. Readers fetch
those from the CDN over HTTP. When new assets are published the web server
pushes them up to the CDN's bucket.

There is an admin who maintains the database directly rather than through the
CMS. That is an unsecured MySQL connection straight to the box, no TLS on it,
and they do it from wherever they happen to be — there is no jump host.

Nothing here tells you how the push to the CDN is authenticated, and I could
not tell you whether the database is encrypted on disk.
