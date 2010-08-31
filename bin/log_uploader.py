#!/usr/bin/python
"""%prog [options] host builder_path build_number

Uploads logs from build to the given host.
"""
import sys, os, cPickle, gzip, subprocess, time

from buildbot import util
from buildbotcustom.process.factory import postUploadCmdPrefix

def ssh(user, identity, host, remote_cmd, port=22):
    devnull = open(os.devnull)
    cmd = ['ssh', '-l', user]
    if identity:
        cmd.extend(['-i', identity])
    cmd.extend(['-p', str(port), host, remote_cmd])

    proc = subprocess.Popen(cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=devnull,)

    retcode = proc.wait()
    output = proc.stdout.read().strip()
    if retcode != 0:
        raise Exception("Command %s returned non-zero exit code %i:\n%s" % (
            cmd, retcode, output))
    return output

def scp(user, identity, host, files, remote_dir, port=22):
    devnull = open(os.devnull)
    cmd = ['scp']
    if identity:
        cmd.extend(['-i', identity])
    cmd.extend(['-P', str(port)])
    cmd.extend(files)
    cmd.append("%s@%s:%s" % (user, host, remote_dir))

    proc = subprocess.Popen(cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=devnull,)

    retcode = proc.wait()
    output = proc.stdout.read().strip()
    if retcode != 0:
        raise Exception("Command %s returned non-zero exit code %i:\n%s" % (
            cmd, retcode, output))
    return output

def getBuild(builder_path, build_number):
    build_path = os.path.join(builder_path, build_number)

    if not os.path.exists(build_path):
        raise ValueError("Couldn't find %s" % build_path)

    class FakeBuilder:
        basedir = builder_path
        name = os.path.basename(builder_path)

    build = cPickle.load(open(build_path))
    build.builder = FakeBuilder()
    return build

def getAuthor(build):
    props = build.getProperties()
    if 'who' in props:
        return props['who']

    changes = build.getSourceStamp().changes
    if changes:
        return changes[0].who

def getBuildId(build):
    return build.getProperty('buildid')

def formatLog(tmpdir, build):
    """
    Returns a filename with the contents of the build log
    written to it.
    """
    builder_name = build.builder.name
    build_name = "%s-build%s.txt.gz" % (builder_name, build_number)

    logFile = gzip.GzipFile(os.path.join(tmpdir, build_name), "w")

    for step in build.getSteps():
        times = step.getTimes()
        if not times or not times[0]:
            elapsed = "not started"
        elif not times[1]:
            elapsed = "not started"
        else:
            elapsed = util.formatInterval(times[1] - times[0])

        results = step.getResults()[0]
        if results == (None, []):
            results = "not started"

        shortText = ' '.join(step.getText()) + ' (results: %s, elapsed: %s)' % (results, elapsed)
        logFile.write("========= Started %s ==========\n" % shortText)

        for log in step.getLogs():
            data = log.getTextWithHeaders()
            logFile.write(data)
            if not data.endswith("\n"):
                logFile.write("\n")

        logFile.write("======== Finished %s ========\n\n" % shortText)
    logFile.close()
    return os.path.join(tmpdir, build_name)

if __name__ == "__main__":
    from optparse import OptionParser
    import tempfile, shutil

    parser = OptionParser(__doc__)
    parser.set_defaults(
            nightly=False,
            trybuild=False,
            user=os.environ.get("USER"),
            product="firefox",
            )
    parser.add_option("-u", "--user", dest="user", help="upload user name")
    parser.add_option("-i", "--identity", dest="identity", help="ssh identity")
    parser.add_option("-b", "--branch", dest="branch", help="branch")
    parser.add_option("-p", "--platform", dest="platform", help="platform")
    parser.add_option("--product", dest="product", help="product directory")
    parser.add_option("--nightly", dest="nightly", action="store_true",
            help="upload to nightly dir")
    parser.add_option("--try", dest="trybuild", action="store_true",
            help="upload to try build directory")

    options, args = parser.parse_args()

    if not options.branch:
        parser.error("branch required")

    if not options.platform:
        parser.error("platform required")

    if len(args) != 3:
        parser.error("Need to specify host, builder_path and build number")

    host, builder_path, build_number = args

    local_tmpdir = tempfile.mkdtemp()

    try:
        # Format the log into a compressed text file
        build = getBuild(builder_path, build_number)
        logfile = formatLog(local_tmpdir, build)
        buildid = build.getProperty('buildid')

        # Now....upload it!
        remote_tmpdir = ssh(user=options.user, identity=options.identity, host=host,
                remote_cmd="mktemp -d")
        try:
            scp(user=options.user, identity=options.identity, host=host,
                    files=[logfile], remote_dir=remote_tmpdir)

            remote_files = [os.path.join(remote_tmpdir, os.path.basename(f)) for f in [logfile]]
            uploadArgs = dict(
                upload_dir="%s-%s" % (options.branch, options.platform),
                branch=options.branch,
                product=options.product,
                buildid=buildid,
            )

            if options.trybuild:
                uploadArgs.update(dict(
                    to_try=True,
                    to_tinderbox_dated=False,
                    who=getAuthor(build),
                    revision=build.getProperty('revision')[:12],
                    builddir=build.getProperty('builddir'),
                    ))
            else:
                uploadArgs.update(dict(
                    to_try=False,
                    to_tinderbox_dated=True,
                    who=None,
                    revision=None,
                    builddir=None,
                    ))
            post_upload_cmd = postUploadCmdPrefix(**uploadArgs)
            post_upload_cmd += [remote_tmpdir]
            post_upload_cmd += remote_files
            post_upload_cmd = " ".join(post_upload_cmd)

            print "Running", post_upload_cmd

            print ssh(user=options.user, identity=options.identity, host=host, remote_cmd=post_upload_cmd)
        finally:
            ssh(user=options.user, identity=options.identity, host=host,
                    remote_cmd="rm -rf %s" % remote_tmpdir)

    finally:
        shutil.rmtree(local_tmpdir)