#!/usr/bin/env python
"""Maintain a set of periodic rsync hardlinked backups.

E.g. you can configure the script to retain daily backups for 7 days, weekly
backups for 4 weeks, monthly backups for 6 months, yearly backups forever.

The script maintains a set of backup dirs into a target directory. Backup dirs
have a name ``TYPE-YYYYMMDDTHHMMSS``, such as ``daily-20120807T042155``. The
lates dirs have symlinks `curr` and `prev`.

The tasks of the command are:

- Create a new backup dir if required. It is required when the previous backup
  dir has passed its period length.  E.g. if the rotation is daily and the
  script is invoked at 1 hour time, don't do anything. With this strategy a
  backup run manually at 10 will overwrite one taken by cron at 3. The
  idempotent script allows the command to be run several times, e.g. to bakcup
  many file systems into the same backup dir with different independent
  commands.

- Rotate the backup dirs going out. if a daily backup is past its retention
  period, and the weekly backup is more than 7 days old, the daily backup dir
  becomes a weekly. Otherwise it is deleted.

- Keep the `curr` and `prev` links up to date.

"""

import os
import re
import sys
import stat
import shutil
from datetime import datetime, timedelta
from operator import attrgetter
from collections import namedtuple

import logging
logger = logging.getLogger()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s')

class ScriptError(Exception):
    """Controlled exception raised by the script."""


class BackupDir(namedtuple('BackupDir', "type date")):
    def __str__(self):
        return '%s-%s' % (self.type, self.date.strftime('%Y%m%dT%H%M%S'))

    name_pattern = re.compile(r'^([a-zA-Z0-9_]+)-(\d{8}T\d{6})$')

    @classmethod
    def parse(self, s):
        m = self.name_pattern.match(s)
        if not m:
            raise ValueError("bad backup dir name: '%s'" % s)

        type, date = m.groups()
        try:
            date = datetime.strptime(date, '%Y%m%dT%H%M%S')
        except ValueError:
            raise ValueError("invalid date in dir name: '%s'" % s)

        return BackupDir(type, date)


Period = namedtuple('Period', "name length retention")

# TODO: must be configurable
SCHEDULE = [
    Period('daily',  timedelta(days=1), timedelta(days=7)),
    Period('weekly', timedelta(days=7), timedelta(days=30)),
    Period('monthly', timedelta(days=30), timedelta(days=90)),
    Period('quarterly', timedelta(days=90), timedelta(days=360)),
    Period('yearly', timedelta(days=360), timedelta.max), ]

# SCHEDULE = [
#     Period('L1', timedelta(days=1),   timedelta(days=5)),
#     Period('L2', timedelta(days=5),   timedelta(days=25)),
#     Period('L3', timedelta(days=25),  timedelta(days=125)),
#     Period('L4', timedelta(days=125), timedelta(days=625)),
#     Period('L5', timedelta(days=625), timedelta.max), ]


class DirHandler(object):
    def __init__(self, base_dir, schedule):
        self.base_dir = base_dir
        self.schedule = schedule

    def do_links(self, opt):
        dir = self._choose_current_dir(opt)
        self.create_link(dir, 'curr')

        dir = self._choose_previous_dir(opt)
        if dir:
            self.create_link(dir, 'prev')

    def do_rotate(self, opt):
        curr = self.dir_from_link('curr')
        if not curr:
            raise ScriptError("link 'curr' not found")

        new = self.rotate(curr, opt.date, self.schedule)

        self.clear_link('curr')
        self.clear_link('prev')
        self.create_link(new, 'latest')

    def rotate(self, dir, date, periods):
        if not periods:
            return

        period = periods[0]

        avail = self.get_backup_dirs(type=period.name)
        recent = avail and avail[-1] or None
        if recent and recent.date >= dir.date and recent != dir:
            raise ScriptError("new dir %s not more recent than %s"
                % (avail[-1], dir))

        if not recent or dir.date - recent.date >= period.length:
            dir = self.rename_backup_dir(dir, period.name, dir.date)

            for old in avail:
                if date - old.date >= period.retention:
                    self.rotate(old, date, periods[1:])

            return dir

        else:
            if recent != dir:
                self.delete_backup_dir(dir)
            else:
                return dir

    def _choose_current_dir(self, opt):
        # if there is a current dir, and it's still valid, reuse that one.
        # rename it to the current date though: if it's still there it was
        # probably a failed backup and we can recycle it.
        dirs = self.get_backup_dirs(type='curr')
        if dirs:
            dir = dirs[-1]
            logger.debug("using previous curr dir: %s", dir)
            dir = self.rename_backup_dir(dir, 'curr', opt.date)
            return dir

        # See if there is a dir of the shorter type and is still up to date
        dirs = self.get_backup_dirs(type=self.schedule[0].name)
        if dirs:
            if opt.date < dirs[-1].date:
                raise ScriptError(
                    "available dir %s newer than the date requested %s"
                    % (dirs[-1], opt.date))

            if opt.date - dirs[-1].date < self.schedule[0].length:
                dir = dirs[-1]
                logger.debug("using backup dir still valid: %s", dir)
                return dir

        dir = self.create_backup_dir('curr', opt.date)
        logger.debug("using new backup dir: %s", dir)
        return dir

    def _choose_previous_dir(self, opt):
        curr = self.dir_from_link('curr')
        assert curr
        for period in self.schedule:
            dirs = [ d for d in self.get_backup_dirs(type=period.name)
                if d.date < curr.date ]
            if dirs:
                return dirs[-1]

    def get_backup_dirs(self, type):
        rv = []
        for dir in os.listdir(self.base_dir):
            try:
                dir = BackupDir.parse(dir)
            except ValueError:
                continue

            if dir.type != type:
                continue

            rv.append(dir)

        rv.sort(key=attrgetter('date'))

        return rv

    def create_backup_dir(self, type, date):
        dir = BackupDir(type=type, date=date)
        logger.info("creating backup dir: %s", dir)
        os.mkdir(os.path.join(self.base_dir, str(dir)))
        return dir

    def rename_backup_dir(self, dir, type, date):
        tgtdir = BackupDir(type, date)
        if dir == tgtdir:
            return

        src = os.path.join(self.base_dir, str(dir))
        tgt = os.path.join(self.base_dir, str(tgtdir))

        if os.path.exists(tgt):
            raise ScriptError("can't rename %s: target dir exists: %s"
                % (dir, tgt))

        logger.info("renaming backup dir %s to %s", dir, tgtdir)
        shutil.move(src, tgt)

        return tgtdir

    def delete_backup_dir(self, dir):
        # Guard: don't remove the last dir created. There must be a bug in
        # this case: don't delete the just finished backup!
        if dir == self.dir_from_link('curr'):
            raise ScriptError("not deleting the dir just created: %s" % (dir,))

        logger.info("deleting backup dir: %s", dir)
        fn = os.path.join(self.base_dir, str(dir))
        shutil.rmtree(fn)

    def create_link(self, dir, name):
        self.clear_link(name)

        fn = os.path.join(self.base_dir, name)
        logger.info("creating link '%s' to %s", name, dir)
        os.symlink(str(dir), fn)

    def clear_link(self, name):
        fn = os.path.join(self.base_dir, name)
        # use lstat as os.path.exists and others return None on broken symlinks
        try:
            s = os.lstat(fn)
        except OSError:
            # link doesn't exist
            return

        if stat.S_ISLNK(s.st_mode):
            logger.info("deleting link: %s", name)
            os.unlink(fn)
        else:
            raise ScriptError("can't delete: not a link: '%s'" % fn)

    def dir_from_link(self, name):
        fn = os.path.join(self.base_dir, name)
        if os.path.exists(fn):
            if os.path.islink(fn):
                fn = os.readlink(fn)
                return BackupDir.parse(os.path.basename(fn))
            else:
                raise ScriptError("not a symlink: %s" % fn)


def main():
    opt = parse_cmdline()

    if not os.path.isdir(opt.base_dir):
        raise ScriptError("not a valid directory: '%s'" % opt.base_dir)

    hnd = DirHandler(opt.base_dir, SCHEDULE)
    if opt.mode == 'links':
        hnd.do_links(opt)
    elif opt.mode == 'rotate':
        hnd.do_rotate(opt)
    else:
        assert False, "wat?"

def parse_cmdline():
    modes = ('links', 'rotate')

    from optparse import OptionParser
    parser = OptionParser(usage="%prog [options] DIR",
        description=__doc__)
    parser.add_option('--date', metavar="DATE",
        default=datetime.now(),
        help="the reference date of the backup [default: now]")
    parser.add_option('--mode', metavar="MODE",
        type='choice', choices=modes,
        help="the action to perform (links, rotate)")

    def parse_date(s):
        if s is None or isinstance(s, datetime):
            return s

        dt = re.match(
            r'^(\d\d\d\d)(\d\d)(\d\d)(?:T(\d\d)(?:(\d\d)(?:(\d\d))?)?)?$', s)
        if dt:
            try:
                dt = datetime(*[int(x) if x else 0 for x in dt.groups()])
            except ValueError:
                dt = None

        if not dt:
            parser.error("date not valid: '%s'" % s)

        return dt

    opt, args = parser.parse_args()
    if len(args) != 1:
        parser.error("target DIR required")

    if not opt.mode:
        for mode in modes:
            if os.path.basename(sys.argv[0]) == 'rr' + mode:
                opt.mode = mode
                break

    if not opt.mode:
        parser.error(
            "please specify a --mode or use a specific script name")

    opt.base_dir = args[0]
    opt.date = parse_date(opt.date)

    return opt

if __name__ == '__main__':
    try:
        sys.exit(main())

    except ScriptError, e:
        logger.error("%s", e)
        sys.exit(1)

    except Exception, e:
        logger.error("Unexpected error: %s - %s",
            e.__class__.__name__, e, exc_info=True)
        sys.exit(1)

    except KeyboardInterrupt:
        logger.info("user interrupt")
        sys.exit(1)

