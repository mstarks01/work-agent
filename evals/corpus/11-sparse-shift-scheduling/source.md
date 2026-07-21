Colleague shift scheduling tool.

Store managers use a scheduling web app to build the weekly rota for their
store. Colleagues use the same app to look at the shifts they have been given.
Colleagues reach the app from their own phones.

The web app talks to a scheduling service. The scheduling service is what
actually reads and writes the rotas, which live in a rota database. Colleague
names, contact details and stated availability are held in that database.

Once a week the scheduling service writes a payroll export onto a file share.
The payroll system, which is run by another team, collects the export from the
file share.

The scheduling service, the rota database and the file share are on the
internal network.

That is as much as we have written down. Nobody has documented how colleagues
or managers sign in, how the web app and the scheduling service identify each
other, how the payroll system is identified when it collects the export, or
whether any of it is encrypted.
