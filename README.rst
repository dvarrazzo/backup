piro's backup
=============

My personal solution for backups. Resulting backups are:

- plain files: no zip or tar.
- aperiodic: a backup may start if the PC is on at 3 AM, otherwise no big deal.
- incremental: save only new files, the others are hardlinked.
- keep daily backups for a week, weekly for a month, monthly for a year, yearly
  forever.
- no password sent around or stored in files.

This is an extension on the ``rrsync`` script available online (and included in
the repos as well). ``rrsync`` allows running ``rsync`` into a restricted
directory with args sanitization. The project contains:

``rrlinks``
    A script to create a new backup directory and ``curr`` and ``prev``
    symlinks to the last dirs.

``rrrotate``
    A script to finish the backup, eventually renaming or deleting old backups.

``rrrsync``
    A script to run ``rrlinks``, ``rrsync``, ``rrrotate`` in sequence.


Usage
-----

- on the source host, create a dir, e.g. ``/root/backup`` and chdir into it.

- on the source host, generate a new rsa pair with no passphrase::

    ssh-keygen -f SOURCE.id_rsa -N ''

- on the target host, clone this repository, e.g. in
  ``/usr/local/src/backup``.  You may have to custmize the consts in ``rrsync``

- on the target host, configure sshd to accept at least forced command from
  root, e.g.  add to ``/etc/ssh/sshd_config``::

    PermitRootLogin forced-commands-only

- on the target host, configure the pkey generated above to run the ``rrrsync``
  command, e.g. add to ``/root/.ssh/authorized_keys``::

    command="/usr/local/src/backup/rrrsync /backups" ssh-rsa AAAAB3Nza... root@SOURCE

- on the source host, write a script to perform the backup via rsync and put it
  into crontab, e.g. ``/root/backup/backup.sh``::

    #!/bin/bash

    export OPTS="-axvz --delete --delete-excluded --numeric-ids"
    export SSH='ssh -i SOURCE.id_rsa'

    rsync $OPTS -e "$SSH" --exclude-from=exclude.home --link-dest=/prev/home/ /home/ root@target:curr/home/
    rsync $OPTS -e "$SSH" --exclude-from=exclude.root --link-dest=/prev/root/ / root@target:curr/root/

The above example assumes ``home`` and ``/`` as two separate file systems to be
backed up into different dirs. ``exclude.home`` and ``exclude.root`` are files
to avoid backup (see rsync manpage). ``prev`` and ``curr`` are symlinks
maintained by ``rrlinks``. The result of running the script a couple of times
is dirs on the target host such as::

    /backups/daily-20120810T000000/home
    /backups/daily-20120810T000000/root
    /backups/daily-20120813T003300/home
    /backups/daily-20120813T003300/root


Disclaimer
----------

This stuff has been designed for my own needs and amusement: although I think it
is a good idea, it may be a bad one for you, in which case I hereby forbid you
to sue me. Also, this stuff is GPL to be more enterprise-unfriendly. Have a
nice day!

