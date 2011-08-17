#!/usr/bin/env python

'''
Helpful functions for interacting with LSF using python.

Also sets up a user environment to use LSF.  Originially created to configure the web user, www-data,
but can set up any user, I think.  Simply importing this module will configure the environment.
'''

import re
import os
import subprocess
import time

import execute
import logging


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


def isJobNameOff(jobName, retry=False, delay=1.0):
    '''
    jobName: the name of a lsf job.
    The job is "off" lsf if there is no status for it on LSF (i.e. bjobs returns nothing for it)
    or if its status is DONE, EXIT, or ZOMBIE.
    exception raised if there are multiple jobs for the same name.
    '''
    statuses = getJobNameStatuses(jobName, retry, delay)
    # "on" jobs
    onStatuses = [status for status in statuses if status not in OFF_STATUSES]
    # too many "on" jobs?
    if len(onStatuses) > 1:
        msg = 'more than one LSF job is being processed for {}\nstatuses={}'.format(jobName, onStatuses)
        raise Exception(msg)
    # any "on" jobs?
    return not onStatuses


def isJobNameOn(jobName, retry=False, delay=1.0):
    '''
    checks if job is running, pending, suspended, or otherwise in the process of running on LSF.
    A job is "on" lsf if it exists on lsf and does not have an off status.
    returns: True if job is not off.  False otherwise.
    exception raised if there are multiple jobs for the same name.
    '''
    return not isJobNameOff(jobName, retry, delay)


def getJobNameStatuses(jobName, retry=False, delay=1.0):
    infos = getJobNameInfos(jobName)
    if not infos and retry: # pause to let lsf catch up and try again
        time.sleep(delay);
        infos = getJobNameInfos(jobName)
    return [info[STATUS] for info in infos]


def getJobNameInfos(jobName):
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
    return getJobInfosSub(args)


def getJobStatuses(job, retry=False, delay=1.0):
    infos = getJobInfos([job])
    if not infos and retry: # pause to let lsf catch up and try again
        time.sleep(delay);
        infos = getJobInfos([job])
    return [info[STATUS] for info in infos]


def getJobInfos(jobIds=None):
    '''
    jobIds: seq of job ids to get info for.  If jobIds is None, infos for all lsf jobs are returned.
    returns: info for each job id in jobIds.
    '''

    if jobIds == None:
        jobIds = ['0']

    args = ['bjobs', '-a', '-u', 'all', '-w'] + [str(j) for j in jobIds]
    return getJobInfosSub(args)


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
            userId = m.group(2)
            status = m.group(3)
            queue = m.group(4)
            submissionHost = m.group(5)
            executionHost = m.group(6)
            jobName = m.group(7)
            submitTime = m.group(8)

            info = {JOBID: jobId, STATUS: status, JOB_NAME: jobName}
            infos.append(info)

    return infos
    
    

####################################################################################
# FUNCTIONS TO SUBMIT COMMANDS TO LSF INCLUDING DEPENDENCIES AND ERROR HANDLING JOBS
####################################################################################

def waitForJobsOption(jobIds):
    '''
    jobIds: sequence of lsf job ids
    returns: dependency expression that runs if all of the jobs become done or any of the jobs exit.
    '''
    # e.g. -w '(done(1) && ... && done(n)) || exit(1) || ... || exit(n)'
    doneList = ['done('+id+')' for id in jobIds]
    exitList = ['exit('+id+')' for id in jobIds]
    doneExpr = '('+' && '.join(doneList)+')'
    exitExpr = ' || '.join(exitList)
    dependencyExpr = "-w '"+doneExpr+" || "+exitExpr+"'"
    return dependencyExpr


def submitCheckJobs(jobids, cmds, dependentJobIds=None):
    '''
    for each main job in jobids:
       submits a error checking job lsf which checks if the main job exited
       with an error and if so logs an error message and kills any dependent Jobs.
       Also submits a job which, when the main job exits cleanly kills the error checking job.
    '''
    
    #iterate over ids and cmds in parallel by using map
    for jobid, cmd in map(None, jobids, cmds):
        queueOption = '-q shared_15m'
        jobName = 'exit'+jobid
        jobNameOption = '-J '+jobName
        emailOption = '-N'
        exitOption = "-w 'exit("+jobid+")'"
        echoErrorMsgCmd = "echo '[error] job "+jobid+" failed. command was "+str(cmd)+"'"
        exitCmds = [echoErrorMsgCmd]
        if dependentJobIds:
            killDependentJobsCmd = 'bkill '+(' '.join(dependentJobIds))
            exitCmds.append(killDependentJobsCmd)
        exitid = submitToLSF(exitCmds, [queueOption, emailOption, jobNameOption, exitOption, '-o %J.out'])

        endedOption = "-w 'done("+jobid+") || ended("+exitid+")'"
        killJobNameCmd = 'bkill '+jobNameOption
        submitToLSF([killJobNameCmd], [queueOption, endedOption, '-o %J.out'])
    return


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


def submitToLSF(cmds, bsubOptions=None):
    '''
    cmds: seq of command lines to be submitted to lsf via bsub.  I think they run in a shell.
    The lsf job exits with the exit code of the last command run.  So if all the commands have non-zero exit codes except the last,
    the job status will be DONE.  Try cmds = [cmd+' || exit' for cmd in cmds] to make the job end with EXIT after the first non-zero exit code.
    bsubOptions: an optional list of options to bsub command.
    Runs one bsub command and pipes each command in cmds to the bsub command.
    returns: job id of the bsub submission
    throws: exception if the bsub submission fails.
    warning: may not work with synchronous/interactive bsub commands (e.g. with -K or -I options.)
    example:
    queueOption = '-q shared_15m'
    jobName = 'exit'+jobid
    jobNameOption = '-J '+jobName
    emailOption = '-N'
    cmds = ['mycommand']
    jobId = LSF.submitToLSF(cmds, [queueOption, emailOption, jobNameOption, ' -o /dev/null'])
    '''

    if bsubOptions is None:
        bsubOptions = []
    
    # new way: raise exception when bsub command fails
    opts = ' '.join(bsubOptions)
    cmd = 'bsub '+opts
    stdin = '\n'.join(cmds)
    logging.debug('submitToLSF(): cmd: %s'%cmd)
    logging.debug('submitToLSF(): stdin: %s'%stdin)
    output = execute.run(cmd, stdin)

    # get job id from bsub output
    m = re.search('<(\d+)>', output)
    if m:
        jobid = m.group(1)
        # logging.debug('[bsub]  '+str(bsubOptions)+' '+str(cmds))
        # logging.debug('[bsub] results in %s.out' % jobid)
    else:
        raise Exception('submitToLSF: failed to find job id for submitted job.  submission output='+str(output))

    return jobid


def bsub(cmds=None, queue=None, interactive=False, outputFile=None):
    '''
    executes the list of cmds on the queue, possibly interactively,
    and possibly redirects the output to a file (any "%J" in the filename
    will be replaced with the lsf job id.
    '''
    # logging.debug('in lsf.bsub()')
    if cmds==None: cmds = []
    
    if interactive: interactiveOption = '-I'
    else: interactiveOption = ''

    if outputFile: outputOption = '-o '+outputFile
    else: outputOption = ''

    if queue: queueOption = ' -q '+queue
    else: queueOption = ''
    
    bsubcmd = 'bsub '+interactiveOption+' '+queueOption+' '+outputOption

    pin, pout = os.popen2(bsubcmd)
    for cmd in cmds:
        pin.write(cmd+'\n')
    pin.close()
    cmdout = pout.read()
    exitcode = pout.close()
    return exitcode



########################################
# SETTING UP USER ENVIRONMENT TO USE LSF
########################################

def setEnviron():
    LSF_DIR = '/opt/lsf/7.0/linux2.6-glibc2.3-x86_64' # '/opt/lsf/6.0/linux2.4-glibc2.3-x86'
    BIN_DIR = os.path.join(LSF_DIR, 'bin')
    CONF_DIR = '/opt/lsf/conf'
    LIB_DIR = os.path.join(LSF_DIR, 'lib')
    SERVER_DIR = os.path.join(LSF_DIR, 'etc')

    os.environ['LSF_BINDIR'] = BIN_DIR
    os.environ['LSF_ENVDIR'] = CONF_DIR
    os.environ['LSF_LIBDIR'] = LIB_DIR
    os.environ['LSF_SERVERDIR'] = SERVER_DIR
    if os.environ.has_key('PATH'):
        os.environ['PATH'] = os.environ['PATH'] + os.pathsep + BIN_DIR
    else:
        os.environ['PATH'] = BIN_DIR
    return


setEnviron()



################
# MAIN MAIN MAIN
################


def main():
    import sys, time
    
    # use job name to keep track of a logical group of commands
    jobName = str(time.time())
    
    # run commands in parallel on lsf, sending their output to a file
    cmds = [l.strip() for l in sys.stdin.readlines()]   
    num = 0 # to name output files (jblast already has a better way of doing this, I think)
    for cmd in cmds:
        bsub = 'bsub -q rodeo_unlimited -N -o foo'+str(num)+' -J "'+jobName+'" '+cmd
        num += 1
        os.system(bsub)
        
    # run cmd to concatenate all output files of cmds above.
    # if there are hundreds of files, this simple approach might fail
    cmd = 'cat '+' '.join(['foo'+str(n) for n in range(num)])
    bsub = 'bsub -K -q rodeo_15m -w \'ended("'+jobName+'")\' '+cmd
    os.sytem(bsub)
      
   

if __name__ == '__main__':
    
    main()
