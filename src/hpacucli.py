#
# Copyright (C) 2013 eNovance SAS <licensing@enovance.com>
#
# Author: Frederic Lepied <frederic.lepied@enovance.com>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

'''API to the hpacucli utility. Can be used to gather informations
about disks, RAID arrays or controllers or to configure them.
'''

import os
import re
import pexpect


class Error(Exception):
    'Exception class to capture errors while calling hpacucli sub-commands.'
    def __init__(self, value):
        super(Error, self).__init__()
        self.value = value

    def __str__(self):
        return repr(self.value)


ALL_SHOW_REGEXP = re.compile(r'^(.*) in Slot ([0-9]+).*\(sn: (.*)\)', re.M)


def parse_ctrl_all_show(output):
    'Parse the output of the "ctrl <sel> all show" hpacucli sub-command.'
    lst = []
    for line in output.split('\n'):
        res = ALL_SHOW_REGEXP.search(line)
        if res:
            lst.append((int(res.group(2)), res.group(1), res.group(3)))
    return lst

PHYSICAL_REGEXP = re.compile(r'\s*physicaldrive (.*) \(.*, (.*), (.*), (.*)\)')
LOGICAL_REGEXP = re.compile(r'\s*logicaldrive (.*) \((.*), (.*), (.*)\)')


def _parse_ctrl_d_all_show(output, regexp):
    '''Parse lines like:

   array C

      physicaldrive 1I:1:2 (port 1I:box 1:bay 2, SATA, 1 TB, OK)

into an associative array with the matching result like:

[('array C', [('1I:1:2', 'SATA', '1 TB', 'OK')]),]

The regexp arg must extract the 4 informations from description lines.
'''
    arr = []
    cur = []
    idx = None
    for line in output.split('\n'):
        line = line.strip()
        if line[:5] == 'array' or line == 'unassigned':
            if idx:
                arr.append((idx, cur))
                cur = []
            idx = line
        else:
            res = regexp.search(line)
            if res:
                cur.append(res.groups())

    if idx:
        arr.append((idx, cur))
    return arr


def parse_ctrl_ld_all_show(output):
    'Parse the output of the "ctrl <sel> ld all show" hpacucli sub-command.'
    return _parse_ctrl_d_all_show(output, LOGICAL_REGEXP)


def parse_ctrl_pd_all_show(output):
    'Parse the output of the "ctrl <sel> pd all show" hpacucli sub-command.'
    return _parse_ctrl_d_all_show(output, PHYSICAL_REGEXP)

ERROR_REGEXP = re.compile('Error: (.*)', re.M)


def parse_error(output):
    '''Parse the output of an hpacucli sub-command for an error and raise an
Error exception if one is found.'''
    res = ERROR_REGEXP.search(output)
    if res:
        raise Error(res.group(1))
    return None


def parse_ctrl_ld_show(output):
    'Parse the output of the "ctrl <sel> ld <id> show" hpacucli sub-command.'
    arr = {}
    idx = None
    for line in output.split('\n'):
        if idx is not None:
            res = PHYSICAL_REGEXP.search(line.strip())
            if res:
                arr[idx] = res.group(1)
            else:
                arr[idx] = line
            idx = None
            continue
        res = line.split(':')
        if len(res) == 2:
            if res[1] == '':
                idx = res[0].strip()
            else:
                idx = None
                arr[res[0].strip()] = res[1].strip()
    return arr

PROMPT_REGEXP = re.compile('=> ')


class Cli:
    '''Class to launch an hpacucli command in the background and to
interact with it to configure or gather information.'''
    def __init__(self, debug=False):
        self.process = None
        self.debug = debug

    def launch(self):
        '''Launch an hpacucli from /usr/sbin. Must be called before
any other method.'''
        if os.path.exists('/usr/sbin/hpacucli'):
            try:
                self.process = pexpect.spawn('/usr/sbin/hpacucli')
                self.process.expect(PROMPT_REGEXP)
            except (OSError, pexpect.EOF, pexpect.TIMEOUT):
                return False
            return True
        else:
            return False

    def _sendline(self, line):
        '''Internal method to send a command to the hpacucli, wait for
the prompt and return the output string.'''
        if self.debug:
            print line
        self.process.sendline(line)
        try:
            self.process.expect(PROMPT_REGEXP)
            ret = self.process.before[len(line):]
        except pexpect.TIMEOUT:
            ret = 'Error: timeout'

        parse_error(ret)
        return ret

    def ctrl_all_show(self):
        '''Send the "ctrl all show" sub-command and return its output
parsed in a structured data.'''
        return parse_ctrl_all_show(
            self._sendline('ctrl all show'))

    def ctrl_pd_all_show(self, selector):
        '''Send the "ctrl <selector> pd all show" sub-command and
return its output parsed in a structured data.'''
        return parse_ctrl_pd_all_show(
            self._sendline('ctrl %s pd all show' % selector))

    def ctrl_ld_all_show(self, selector):
        '''Send the "ctrl <selector> ld all show" sub-command and
return its output parsed in a structured data.'''
        return parse_ctrl_ld_all_show(
            self._sendline('ctrl %s ld all show' % selector))

    def ctrl_ld_show(self, selector, ldid):
        '''Send the "ctrl <selector> ld <ldid> show" sub-command and
return its output parsed in a structured data.'''
        return parse_ctrl_ld_show(
            self._sendline('ctrl %s ld %s show' % (selector, ldid)))

    def ctrl_delete(self, selector):
        '''Send the "ctrl <selector> delete forced" sub-command.'''
        self._sendline('ctrl %s delete forced' % selector)
        return True

    def ctrl_create_ld(self, selector, drives, raid):
        '''Send the "ctrl <selector> create type=ld drives=<drives>
raid=<raid>" sub-command and return the created device name like
/dev/sda.'''
        self._sendline(
            'ctrl %s create type=ld drives=%s raid=%s' %
            (selector, ','.join(drives), raid))
        lds = self.ctrl_ld_all_show(selector)
        # get the last created ld which is the one just created
        last_created_id = lds[-1][1][0][0]
        info = self.ctrl_ld_show(selector, last_created_id)
        return info['Disk Name']

# hpacucli.py ends here
