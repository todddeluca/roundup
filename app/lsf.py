#!/usr/bin/env python

'''
Helpful functions for interacting with LSF using python, and for setting up an environment for lsf.
'''

import collections
import logging
import os
import re
import shlex
import subprocess
import time



###########
# CONSTANTS
###########

STATUS = 'status'
JOBID = 'jobid'
JOB_NAME = 'job_name' # often corresponds to the command line being executed.
# USER = 'user'
# SUBMIT_TIME = 'submit_time'
# SUBMIT_HOST = 'submit_host'
# EXEC_HOST = 'exec_host'
# QUEUE = 'queue'

EXIT_STATUS = 'EXIT'
DONE_STATUS = 'DONE'
ZOMBIE_STATUS = 'ZOMBI'
OFF_STATUSES = (DONE_STATUS, EXIT_STATUS, ZOMBIE_STATUS)


##########################
# JOB MONITORING FUNCTIONS
##########################

def isJobNameOff(jobName, retry=False, delay=1.0):
    '''
    jobName: the name of a lsf job.
    The job is "off" lsf if there is no status for it on LSF (i.e. bjobs returns nothing for it)
    or if its status is DONE, EXIT, or ZOMBIE.
    '''
    infos = getJobNameInfos(jobName, retry, delay)
    return infosAreOff(infos)


def isJobNameOn(jobName, retry=False, delay=1.0):
    '''
    checks if job is running, pending, suspended, or otherwise in the process of running on LSF.
    A job is "on" lsf if it exists on lsf and has a non-"off" status
    returns: True if job is not off.  False otherwise.
    exception raised if there are multiple jobs for the same name.
    '''
    return not isJobNameOff(jobName, retry, delay)


def infosAreOff(infos):
    '''
    infos: a list of job infos.  The are considered "off" if infos is empty
    or if all the info statuses are one of DONE, EXIT, or ZOMBIE.
    '''
    # "on" jobs
    ons = [info for info in infos if info[STATUS] not in OFF_STATUSES]
    return not ons

    

def infosAreOn(infos):
    '''
    infos: a list of job infos.  The are considered "on" if there is one
    info with a status not in DONE, EXIT, or ZOMBIE.
    '''
    return not infosAreOff(infos)
    

def getOnJobNames():
    '''
    return: list of all job names "on" on lsf.
    '''
    jobNameToInfos = getJobNameToInfos()
    return [name for name in jobNameToInfos if infosAreOn(jobNameToInfos[name])]


def getJobNameToInfos():
    jobNameToInfos = collections.defaultdict(list)
    for info in getJobInfos():
        jobNameToInfos[info[JOB_NAME]].append(info)
    return jobNameToInfos


def getJobNameStatuses(jobName, retry=False, delay=1.0):
    infos = getJobNameInfos(jobName)
    if not infos and retry: # pause to let lsf catch up and try again
        time.sleep(delay);
        infos = getJobNameInfos(jobName)
    return [info[STATUS] for info in infos]


def getJobNameInfos(jobName, retry=False, delay=1.0):
    '''
    jobName: name of a job
    returns: info for each job named jobName.
    '''
    
    # td23@orchestra:~$ bjobs -J foo
    # JOBID   USER    STAT  QUEUE      FROM_HOST   EXEC_HOST   JOB_NAME   SUBMIT_TIME
    # 461830  td23    RUN   cbi_unlimited orchestra.med.harvard.edu viola073.cl.med.harvard.edu foo        Mar  3 10:36
    # 461828  td23    PEND  shared_unlimited orchestra.med.harvard.edu    -        foo        Mar  3 10:33
    # td23@orchestra:~$ bjobs -J bar
    # Job <bar> is not found
    
    args = ['bjobs', '-a', '-u', 'all', '-w', '-J', str(jobName)]
    infos = getJobInfosSub(args)
    if not infos and retry: # pause to let lsf catch up and try again
        time.sleep(delay);
        infos = getJobInfosSub(args)
    return infos


def getJobInfos(jobIds=None, retry=False, delay=1.0):
    '''
    jobIds: seq of job ids to get info for.  If jobIds is None, infos for all lsf jobs are returned.
    returns: info for each job id in jobIds.
    '''

    if jobIds == None:
        jobIds = ['0']

    args = ['bjobs', '-a', '-u', 'all', '-w'] + [str(j) for j in jobIds]
    infos = getJobInfosSub(args)
    if not infos and retry: # pause to let lsf catch up and try again
        time.sleep(delay);
        infos = getJobInfosSub(args)
    return infos


def getJobInfosSub(args):
    # jobid, userid, status, queue, submission host, execution host, command/jobname, month, day, time
    # example lines of bjobs -wu all
    # 209991  at55    RUN   all_12h    orchestra.med.harvard.edu cello153.cl.med.harvard.edu /home/np29/biology/admix/release7/mcmc -p parc2 Feb  7 13:42
    # 114345  td23    PEND  shared_unlimited orchestra.med.harvard.edu    -        /home/td23/dev/blastparallel/trunk/pblast.pl -d /groups/rodeo/databases/blast/Caenorhabditis_elegans.aa -p blastp -e 10 -matrix BLOSUM62 -i /home/td23/mito.faa --queue rodeo_unlimited Jul  5 14:22
    # captures: jobid, userid, status, queue, submission host, execution host, command/jobname, submission time
    bjobsRegex = '^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.*?)\s+(\S+\s+\S+\s+\S+)\s+$'

    output = subprocess.check_output(args)
    jobs = output.splitlines(True) # keep line endings

    infos = []
    for job in jobs:
        m = re.search(bjobsRegex, job)
        if m:
            jobId = m.group(1)
            # userId = m.group(2)
            status = m.group(3)
            # queue = m.group(4)
            # submissionHost = m.group(5)
            # executionHost = m.group(6)
            jobName = m.group(7)
            # submitTime = m.group(8)

            info = {JOBID: jobId, STATUS: status, JOB_NAME: jobName}
            infos.append(info)

    return infos
    
    

##########################
# JOB SUBMISSION FUNCTIONS
##########################

# def submitToLSF(cmds, bsubOptions=None):
def bsub(cmd, options=None):
    '''
    This function runs a command on lsf.
    Bsub, the options tokens, and the cmd tokens are run via the subprocess module with shell=False to avoid some kinds of shell injection attacks.
    e.g cmd = 'echo "hi world"', options = ['-J', 'myjob'] -> ['bsub', '-J', 'myjob', 'echo', '"hi world"']
    However see the security warning below.

    WARNING: Not secure against shell injection attacks.  Untrusted commands must have dangerous characters removed despite being run without a shell
    because of how bsub reassembles tokens into a command to run.
    Examples showing how bsub dangerously assembles a command from tokens:
    python -c 'import subprocess; subprocess.check_output(["bsub", "-q", "shared_15m", "cat", "foo.txt;", "rm", "foo.txt"])'
    823087  td23    PEND  shared_15m balcony.orchestra    -        cat foo.txt; rm foo.txt Aug 18 13:37
    python -c 'import subprocess; subprocess.check_output(["bsub", "-q", "shared_15m", "cat", "foo.txt; rm foo.txt"])'
    823126  td23    PEND  shared_15m balcony.orchestra    -        cat "foo.txt; rm foo.txt" Aug 18 13:39
    python -c 'import subprocess; subprocess.check_output(["bsub", "-q", "shared_15m", "cat foo.txt; rm foo.txt"])'
    823151  td23    PEND  shared_15m balcony.orchestra    -        cat foo.txt; rm foo.txt Aug 18 13:40

    cmd: the command to submit to lsf, either as a string or list of tokens.  If cmd is a string, it will be tokenized with shlex.split().
    options: list of options for the bsub command.  These should be tokenized.  e.g. ['-q', 'shared_2h', '-o', '/dev/null', '-J', 'myjob']
    returns: job id of the bsub submission
    raises: exception if the bsub submission fails.
    '''
    if options is None:
        options = []
    if isinstance(cmd, basestring):
        cmd = shlex.split(cmd)
    bsubCmd = ['bsub'] + list(options) + list(cmd)

    logging.debug('bsub(): bsubCmd: {}'.format(bsubCmd))
    output = subprocess.check_output(bsubCmd)

    # get job id from bsub output
    m = re.search('<(\d+)>', output)
    if m:
        jobid = m.group(1)
    else:
        raise Exception('bsub(): failed to find job id for submitted job.  submission output='+str(output))

    return jobid



#############################
# LSF CONFIGURATION FUNCTIONS
#############################

def setEnviron(lsfDir, confDir):
    '''
    Sets the environment variables to run LSF commands.
    lsfDir: e.g. '/opt/lsf/7.0/linux2.6-glibc2.3-x86_64'
    confDir: e.g. '/opt/lsf/conf'
    '''
    binDir = os.path.join(lsfDir, 'bin')
    os.environ['LSF_BINDIR'] = binDir
    os.environ['LSF_ENVDIR'] = confDir
    os.environ['LSF_LIBDIR'] = os.path.join(lsfDir, 'lib')
    os.environ['LSF_SERVERDIR'] = os.path.join(lsfDir, 'etc')
    os.environ['XLSF_UIDDIR'] = os.path.join(lsfDir, 'lib', 'uid')
    if not os.environ.has_key('PATH'):
        os.environ['PATH'] = binDir
    elif binDir not in os.environ['PATH'].split(os.pathsep):
        os.environ['PATH'] = os.environ['PATH'] + os.pathsep + binDir


################
# MAIN MAIN MAIN
################


if __name__ == '__main__':
    pass


#################
# DEPRECATED CODE
#################

def makeDependencyExpression(condition, jobids, booleanOperator):
    '''
    Usage: useful to create a dependency expression for one set of jobs that depend on another
    set, to be passed in the list of bsubOptions given to submitToLSF.
    condition = 'done', 'exit', 'ended'
    booleanOperator = '&&', '||'
    '''
    conds = [condition+'('+jobid+')' for jobid in jobids]
    joinedConds = (' '+booleanOperator+' ').join(conds)
    return " -w '%s' " % joinedConds


