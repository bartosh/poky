import unittest
import os
import logging
import re
import shutil
import tempfile
import glob

import oeqa.utils.ftools as ftools
from oeqa.selftest.base import oeSelfTest
from oeqa.utils.commands import runCmd, bitbake, get_bb_var, create_temp_layer, runqemu
from oeqa.utils.decorators import testcase

class DevtoolBase(oeSelfTest):

    def _test_recipe_contents(self, recipefile, checkvars, checkinherits):
        with open(recipefile, 'r') as f:
            for line in f:
                if '=' in line:
                    splitline = line.split('=', 1)
                    var = splitline[0].rstrip()
                    value = splitline[1].strip().strip('"')
                    if var in checkvars:
                        needvalue = checkvars.pop(var)
                        self.assertEqual(value, needvalue, 'values for %s do not match' % var)
                if line.startswith('inherit '):
                    inherits = line.split()[1:]

        self.assertEqual(checkvars, {}, 'Some variables not found: %s' % checkvars)

        for inherit in checkinherits:
            self.assertIn(inherit, inherits, 'Missing inherit of %s' % inherit)

    def _check_bbappend(self, testrecipe, recipefile, appenddir):
        result = runCmd('bitbake-layers show-appends', cwd=self.builddir)
        resultlines = result.output.splitlines()
        inrecipe = False
        bbappends = []
        bbappendfile = None
        for line in resultlines:
            if inrecipe:
                if line.startswith(' '):
                    bbappends.append(line.strip())
                else:
                    break
            elif line == '%s:' % os.path.basename(recipefile):
                inrecipe = True
        self.assertLessEqual(len(bbappends), 2, '%s recipe is being bbappended by another layer - bbappends found:\n  %s' % (testrecipe, '\n  '.join(bbappends)))
        for bbappend in bbappends:
            if bbappend.startswith(appenddir):
                bbappendfile = bbappend
                break
        else:
            self.assertTrue(False, 'bbappend for recipe %s does not seem to be created in test layer' % testrecipe)
        return bbappendfile

    def _create_temp_layer(self, templayerdir, addlayer, templayername, priority=999, recipepathspec='recipes-*/*'):
        create_temp_layer(templayerdir, templayername, priority, recipepathspec)
        if addlayer:
            self.add_command_to_tearDown('bitbake-layers remove-layer %s || true' % templayerdir)
            result = runCmd('bitbake-layers add-layer %s' % templayerdir, cwd=self.builddir)

    def _process_ls_output(self, output):
        """
        Convert ls -l output to a format we can reasonably compare from one context
        to another (e.g. from host to target)
        """
        filelist = []
        for line in output.splitlines():
            splitline = line.split()
            # Remove trailing . on perms
            splitline[0] = splitline[0].rstrip('.')
            # Remove leading . on paths
            splitline[-1] = splitline[-1].lstrip('.')
            # Drop fields we don't want to compare
            del splitline[7]
            del splitline[6]
            del splitline[5]
            del splitline[4]
            del splitline[1]
            filelist.append(' '.join(splitline))
        return filelist


class DevtoolTests(DevtoolBase):

    @testcase(1158)
    def test_create_workspace(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        result = runCmd('bitbake-layers show-layers')
        self.assertTrue('/workspace' not in result.output, 'This test cannot be run with a workspace layer in bblayers.conf')
        # Try creating a workspace layer with a specific path
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        self.track_for_cleanup(tempdir)
        result = runCmd('devtool create-workspace %s' % tempdir)
        self.assertTrue(os.path.isfile(os.path.join(tempdir, 'conf', 'layer.conf')), msg = "No workspace created. devtool output: %s " % result.output)
        result = runCmd('bitbake-layers show-layers')
        self.assertIn(tempdir, result.output)
        # Try creating a workspace layer with the default path
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        result = runCmd('devtool create-workspace')
        self.assertTrue(os.path.isfile(os.path.join(workspacedir, 'conf', 'layer.conf')), msg = "No workspace created. devtool output: %s " % result.output)
        result = runCmd('bitbake-layers show-layers')
        self.assertNotIn(tempdir, result.output)
        self.assertIn(workspacedir, result.output)

    @testcase(1159)
    def test_devtool_add(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        # Fetch source
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        self.track_for_cleanup(tempdir)
        url = 'http://www.ivarch.com/programs/sources/pv-1.5.3.tar.bz2'
        result = runCmd('wget %s' % url, cwd=tempdir)
        result = runCmd('tar xfv pv-1.5.3.tar.bz2', cwd=tempdir)
        srcdir = os.path.join(tempdir, 'pv-1.5.3')
        self.assertTrue(os.path.isfile(os.path.join(srcdir, 'configure')), 'Unable to find configure script in source directory')
        # Test devtool add
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake -c cleansstate pv')
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        result = runCmd('devtool add pv %s' % srcdir)
        self.assertTrue(os.path.exists(os.path.join(workspacedir, 'conf', 'layer.conf')), 'Workspace directory not created')
        # Test devtool status
        result = runCmd('devtool status')
        self.assertIn('pv', result.output)
        self.assertIn(srcdir, result.output)
        # Clean up anything in the workdir/sysroot/sstate cache (have to do this *after* devtool add since the recipe only exists then)
        bitbake('pv -c cleansstate')
        # Test devtool build
        result = runCmd('devtool build pv')
        installdir = get_bb_var('D', 'pv')
        self.assertTrue(installdir, 'Could not query installdir variable')
        bindir = get_bb_var('bindir', 'pv')
        self.assertTrue(bindir, 'Could not query bindir variable')
        if bindir[0] == '/':
            bindir = bindir[1:]
        self.assertTrue(os.path.isfile(os.path.join(installdir, bindir, 'pv')), 'pv binary not found in D')

    @testcase(1162)
    def test_devtool_add_library(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        # We don't have the ability to pick up this dependency automatically yet...
        bitbake('libusb1')
        # Fetch source
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        self.track_for_cleanup(tempdir)
        url = 'http://www.intra2net.com/en/developer/libftdi/download/libftdi1-1.1.tar.bz2'
        result = runCmd('wget %s' % url, cwd=tempdir)
        result = runCmd('tar xfv libftdi1-1.1.tar.bz2', cwd=tempdir)
        srcdir = os.path.join(tempdir, 'libftdi1-1.1')
        self.assertTrue(os.path.isfile(os.path.join(srcdir, 'CMakeLists.txt')), 'Unable to find CMakeLists.txt in source directory')
        # Test devtool add (and use -V so we test that too)
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        result = runCmd('devtool add libftdi %s -V 1.1' % srcdir)
        self.assertTrue(os.path.exists(os.path.join(workspacedir, 'conf', 'layer.conf')), 'Workspace directory not created')
        # Test devtool status
        result = runCmd('devtool status')
        self.assertIn('libftdi', result.output)
        self.assertIn(srcdir, result.output)
        # Clean up anything in the workdir/sysroot/sstate cache (have to do this *after* devtool add since the recipe only exists then)
        bitbake('libftdi -c cleansstate')
        # Test devtool build
        result = runCmd('devtool build libftdi')
        staging_libdir = get_bb_var('STAGING_LIBDIR', 'libftdi')
        self.assertTrue(staging_libdir, 'Could not query STAGING_LIBDIR variable')
        self.assertTrue(os.path.isfile(os.path.join(staging_libdir, 'libftdi1.so.2.1.0')), "libftdi binary not found in STAGING_LIBDIR. Output of devtool build libftdi %s" % result.output)
        # Test devtool reset
        stampprefix = get_bb_var('STAMP', 'libftdi')
        result = runCmd('devtool reset libftdi')
        result = runCmd('devtool status')
        self.assertNotIn('libftdi', result.output)
        self.assertTrue(stampprefix, 'Unable to get STAMP value for recipe libftdi')
        matches = glob.glob(stampprefix + '*')
        self.assertFalse(matches, 'Stamp files exist for recipe libftdi that should have been cleaned')
        self.assertFalse(os.path.isfile(os.path.join(staging_libdir, 'libftdi1.so.2.1.0')), 'libftdi binary still found in STAGING_LIBDIR after cleaning')

    @testcase(1160)
    def test_devtool_add_fetch(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        # Fetch source
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        self.track_for_cleanup(tempdir)
        testver = '0.23'
        url = 'https://pypi.python.org/packages/source/M/MarkupSafe/MarkupSafe-%s.tar.gz' % testver
        testrecipe = 'python-markupsafe'
        srcdir = os.path.join(tempdir, testrecipe)
        # Test devtool add
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake -c cleansstate %s' % testrecipe)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        result = runCmd('devtool add %s %s -f %s' % (testrecipe, srcdir, url))
        self.assertTrue(os.path.exists(os.path.join(workspacedir, 'conf', 'layer.conf')), 'Workspace directory not created. %s' % result.output)
        self.assertTrue(os.path.isfile(os.path.join(srcdir, 'setup.py')), 'Unable to find setup.py in source directory')
        # Test devtool status
        result = runCmd('devtool status')
        self.assertIn(testrecipe, result.output)
        self.assertIn(srcdir, result.output)
        # Check recipe
        recipefile = get_bb_var('FILE', testrecipe)
        self.assertIn('%s.bb' % testrecipe, recipefile, 'Recipe file incorrectly named')
        checkvars = {}
        checkvars['S'] = '${WORKDIR}/MarkupSafe-%s' % testver
        checkvars['SRC_URI'] = url
        self._test_recipe_contents(recipefile, checkvars, [])
        # Try with version specified
        result = runCmd('devtool reset -n %s' % testrecipe)
        shutil.rmtree(srcdir)
        result = runCmd('devtool add %s %s -f %s -V %s' % (testrecipe, srcdir, url, testver))
        self.assertTrue(os.path.isfile(os.path.join(srcdir, 'setup.py')), 'Unable to find setup.py in source directory')
        # Test devtool status
        result = runCmd('devtool status')
        self.assertIn(testrecipe, result.output)
        self.assertIn(srcdir, result.output)
        # Check recipe
        recipefile = get_bb_var('FILE', testrecipe)
        self.assertIn('%s_%s.bb' % (testrecipe, testver), recipefile, 'Recipe file incorrectly named')
        checkvars = {}
        checkvars['S'] = '${WORKDIR}/MarkupSafe-${PV}'
        checkvars['SRC_URI'] = url.replace(testver, '${PV}')
        self._test_recipe_contents(recipefile, checkvars, [])

    @testcase(1161)
    def test_devtool_add_fetch_git(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        # Fetch source
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        self.track_for_cleanup(tempdir)
        url = 'git://git.yoctoproject.org/libmatchbox'
        checkrev = '462f0652055d89c648ddd54fd7b03f175c2c6973'
        testrecipe = 'libmatchbox2'
        srcdir = os.path.join(tempdir, testrecipe)
        # Test devtool add
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake -c cleansstate %s' % testrecipe)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        result = runCmd('devtool add %s %s -f %s' % (testrecipe, srcdir, url))
        self.assertTrue(os.path.exists(os.path.join(workspacedir, 'conf', 'layer.conf')), 'Workspace directory not created: %s' % result.output)
        self.assertTrue(os.path.isfile(os.path.join(srcdir, 'configure.ac')), 'Unable to find configure.ac in source directory')
        # Test devtool status
        result = runCmd('devtool status')
        self.assertIn(testrecipe, result.output)
        self.assertIn(srcdir, result.output)
        # Check recipe
        recipefile = get_bb_var('FILE', testrecipe)
        self.assertIn('_git.bb', recipefile, 'Recipe file incorrectly named')
        checkvars = {}
        checkvars['S'] = '${WORKDIR}/git'
        checkvars['PV'] = '1.0+git${SRCPV}'
        checkvars['SRC_URI'] = url
        checkvars['SRCREV'] = '${AUTOREV}'
        self._test_recipe_contents(recipefile, checkvars, [])
        # Try with revision and version specified
        result = runCmd('devtool reset -n %s' % testrecipe)
        shutil.rmtree(srcdir)
        url_rev = '%s;rev=%s' % (url, checkrev)
        result = runCmd('devtool add %s %s -f "%s" -V 1.5' % (testrecipe, srcdir, url_rev))
        self.assertTrue(os.path.isfile(os.path.join(srcdir, 'configure.ac')), 'Unable to find configure.ac in source directory')
        # Test devtool status
        result = runCmd('devtool status')
        self.assertIn(testrecipe, result.output)
        self.assertIn(srcdir, result.output)
        # Check recipe
        recipefile = get_bb_var('FILE', testrecipe)
        self.assertIn('_git.bb', recipefile, 'Recipe file incorrectly named')
        checkvars = {}
        checkvars['S'] = '${WORKDIR}/git'
        checkvars['PV'] = '1.5+git${SRCPV}'
        checkvars['SRC_URI'] = url
        checkvars['SRCREV'] = checkrev
        self._test_recipe_contents(recipefile, checkvars, [])

    @testcase(1164)
    def test_devtool_modify(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        # Clean up anything in the workdir/sysroot/sstate cache
        bitbake('mdadm -c cleansstate')
        # Try modifying a recipe
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        self.track_for_cleanup(tempdir)
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        self.add_command_to_tearDown('bitbake -c clean mdadm')
        result = runCmd('devtool modify mdadm -x %s' % tempdir)
        self.assertTrue(os.path.exists(os.path.join(tempdir, 'Makefile')), 'Extracted source could not be found')
        self.assertTrue(os.path.isdir(os.path.join(tempdir, '.git')), 'git repository for external source tree not found')
        self.assertTrue(os.path.exists(os.path.join(workspacedir, 'conf', 'layer.conf')), 'Workspace directory not created')
        matches = glob.glob(os.path.join(workspacedir, 'appends', 'mdadm_*.bbappend'))
        self.assertTrue(matches, 'bbappend not created %s' % result.output)
        # Test devtool status
        result = runCmd('devtool status')
        self.assertIn('mdadm', result.output)
        self.assertIn(tempdir, result.output)
        # Check git repo
        result = runCmd('git status --porcelain', cwd=tempdir)
        self.assertEqual(result.output.strip(), "", 'Created git repo is not clean')
        result = runCmd('git symbolic-ref HEAD', cwd=tempdir)
        self.assertEqual(result.output.strip(), "refs/heads/devtool", 'Wrong branch in git repo')
        # Try building
        bitbake('mdadm')
        # Try making (minor) modifications to the source
        result = runCmd("sed -i 's!^\.TH.*!.TH MDADM 8 \"\" v9.999-custom!' %s" % os.path.join(tempdir, 'mdadm.8.in'))
        bitbake('mdadm -c package')
        pkgd = get_bb_var('PKGD', 'mdadm')
        self.assertTrue(pkgd, 'Could not query PKGD variable')
        mandir = get_bb_var('mandir', 'mdadm')
        self.assertTrue(mandir, 'Could not query mandir variable')
        if mandir[0] == '/':
            mandir = mandir[1:]
        with open(os.path.join(pkgd, mandir, 'man8', 'mdadm.8'), 'r') as f:
            for line in f:
                if line.startswith('.TH'):
                    self.assertEqual(line.rstrip(), '.TH MDADM 8 "" v9.999-custom', 'man file not modified. man searched file path: %s' % os.path.join(pkgd, mandir, 'man8', 'mdadm.8'))
        # Test devtool reset
        stampprefix = get_bb_var('STAMP', 'mdadm')
        result = runCmd('devtool reset mdadm')
        result = runCmd('devtool status')
        self.assertNotIn('mdadm', result.output)
        self.assertTrue(stampprefix, 'Unable to get STAMP value for recipe mdadm')
        matches = glob.glob(stampprefix + '*')
        self.assertFalse(matches, 'Stamp files exist for recipe mdadm that should have been cleaned')

    @testcase(1166)
    def test_devtool_modify_invalid(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        # Try modifying some recipes
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        self.track_for_cleanup(tempdir)
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')

        testrecipes = 'perf kernel-devsrc package-index core-image-minimal meta-toolchain packagegroup-core-sdk meta-ide-support'.split()
        # Find actual name of gcc-source since it now includes the version - crude, but good enough for this purpose
        result = runCmd('bitbake-layers show-recipes gcc-source*')
        reading = False
        for line in result.output.splitlines():
            if line.startswith('=='):
                reading = True
            elif reading and not line.startswith(' '):
                testrecipes.append(line.split(':')[0])
        for testrecipe in testrecipes:
            # Check it's a valid recipe
            bitbake('%s -e' % testrecipe)
            # devtool extract should fail
            result = runCmd('devtool extract %s %s' % (testrecipe, os.path.join(tempdir, testrecipe)), ignore_status=True)
            self.assertNotEqual(result.status, 0, 'devtool extract on %s should have failed. devtool output: %s' % (testrecipe, result.output))
            self.assertNotIn('Fetching ', result.output, 'devtool extract on %s should have errored out before trying to fetch' % testrecipe)
            self.assertIn('ERROR: ', result.output, 'devtool extract on %s should have given an ERROR' % testrecipe)
            # devtool modify should fail
            result = runCmd('devtool modify %s -x %s' % (testrecipe, os.path.join(tempdir, testrecipe)), ignore_status=True)
            self.assertNotEqual(result.status, 0, 'devtool modify on %s should have failed. devtool output: %s' %  (testrecipe, result.output))
            self.assertIn('ERROR: ', result.output, 'devtool modify on %s should have given an ERROR' % testrecipe)

    @testcase(1165)
    def test_devtool_modify_git(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        testrecipe = 'mkelfimage'
        src_uri = get_bb_var('SRC_URI', testrecipe)
        self.assertIn('git://', src_uri, 'This test expects the %s recipe to be a git recipe' % testrecipe)
        # Clean up anything in the workdir/sysroot/sstate cache
        bitbake('%s -c cleansstate' % testrecipe)
        # Try modifying a recipe
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        self.track_for_cleanup(tempdir)
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        self.add_command_to_tearDown('bitbake -c clean %s' % testrecipe)
        result = runCmd('devtool modify %s -x %s' % (testrecipe, tempdir))
        self.assertTrue(os.path.exists(os.path.join(tempdir, 'Makefile')), 'Extracted source could not be found')
        self.assertTrue(os.path.isdir(os.path.join(tempdir, '.git')), 'git repository for external source tree not found')
        self.assertTrue(os.path.exists(os.path.join(workspacedir, 'conf', 'layer.conf')), 'Workspace directory not created. devtool output: %s' % result.output)
        matches = glob.glob(os.path.join(workspacedir, 'appends', 'mkelfimage_*.bbappend'))
        self.assertTrue(matches, 'bbappend not created')
        # Test devtool status
        result = runCmd('devtool status')
        self.assertIn(testrecipe, result.output)
        self.assertIn(tempdir, result.output)
        # Check git repo
        result = runCmd('git status --porcelain', cwd=tempdir)
        self.assertEqual(result.output.strip(), "", 'Created git repo is not clean')
        result = runCmd('git symbolic-ref HEAD', cwd=tempdir)
        self.assertEqual(result.output.strip(), "refs/heads/devtool", 'Wrong branch in git repo')
        # Try building
        bitbake(testrecipe)

    @testcase(1167)
    def test_devtool_modify_localfiles(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        testrecipe = 'lighttpd'
        src_uri = (get_bb_var('SRC_URI', testrecipe) or '').split()
        foundlocal = False
        for item in src_uri:
            if item.startswith('file://') and '.patch' not in item:
                foundlocal = True
                break
        self.assertTrue(foundlocal, 'This test expects the %s recipe to fetch local files and it seems that it no longer does' % testrecipe)
        # Clean up anything in the workdir/sysroot/sstate cache
        bitbake('%s -c cleansstate' % testrecipe)
        # Try modifying a recipe
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        self.track_for_cleanup(tempdir)
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        self.add_command_to_tearDown('bitbake -c clean %s' % testrecipe)
        result = runCmd('devtool modify %s -x %s' % (testrecipe, tempdir))
        self.assertTrue(os.path.exists(os.path.join(tempdir, 'configure.ac')), 'Extracted source could not be found')
        self.assertTrue(os.path.exists(os.path.join(workspacedir, 'conf', 'layer.conf')), 'Workspace directory not created')
        matches = glob.glob(os.path.join(workspacedir, 'appends', '%s_*.bbappend' % testrecipe))
        self.assertTrue(matches, 'bbappend not created')
        # Test devtool status
        result = runCmd('devtool status')
        self.assertIn(testrecipe, result.output)
        self.assertIn(tempdir, result.output)
        # Try building
        bitbake(testrecipe)

    @testcase(1169)
    def test_devtool_update_recipe(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        testrecipe = 'minicom'
        recipefile = get_bb_var('FILE', testrecipe)
        src_uri = get_bb_var('SRC_URI', testrecipe)
        self.assertNotIn('git://', src_uri, 'This test expects the %s recipe to NOT be a git recipe' % testrecipe)
        result = runCmd('git status . --porcelain', cwd=os.path.dirname(recipefile))
        self.assertEqual(result.output.strip(), "", '%s recipe is not clean' % testrecipe)
        # First, modify a recipe
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        self.track_for_cleanup(tempdir)
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        # (don't bother with cleaning the recipe on teardown, we won't be building it)
        result = runCmd('devtool modify %s -x %s' % (testrecipe, tempdir))
        # Check git repo
        self.assertTrue(os.path.isdir(os.path.join(tempdir, '.git')), 'git repository for external source tree not found')
        result = runCmd('git status --porcelain', cwd=tempdir)
        self.assertEqual(result.output.strip(), "", 'Created git repo is not clean')
        result = runCmd('git symbolic-ref HEAD', cwd=tempdir)
        self.assertEqual(result.output.strip(), "refs/heads/devtool", 'Wrong branch in git repo')
        # Add a couple of commits
        # FIXME: this only tests adding, need to also test update and remove
        result = runCmd('echo "Additional line" >> README', cwd=tempdir)
        result = runCmd('git commit -a -m "Change the README"', cwd=tempdir)
        result = runCmd('echo "A new file" > devtool-new-file', cwd=tempdir)
        result = runCmd('git add devtool-new-file', cwd=tempdir)
        result = runCmd('git commit -m "Add a new file"', cwd=tempdir)
        self.add_command_to_tearDown('cd %s; rm %s/*.patch; git checkout %s %s' % (os.path.dirname(recipefile), testrecipe, testrecipe, os.path.basename(recipefile)))
        result = runCmd('devtool update-recipe %s' % testrecipe)
        result = runCmd('git status . --porcelain', cwd=os.path.dirname(recipefile))
        self.assertNotEqual(result.output.strip(), "", '%s recipe should be modified' % testrecipe)
        status = result.output.splitlines()
        self.assertEqual(len(status), 3, 'Less/more files modified than expected. Entire status:\n%s' % result.output)
        for line in status:
            if line.endswith('0001-Change-the-README.patch'):
                self.assertEqual(line[:3], '?? ', 'Unexpected status in line: %s' % line)
            elif line.endswith('0002-Add-a-new-file.patch'):
                self.assertEqual(line[:3], '?? ', 'Unexpected status in line: %s' % line)
            elif re.search('%s_[^_]*.bb$' % testrecipe, line):
                self.assertEqual(line[:3], ' M ', 'Unexpected status in line: %s' % line)
            else:
                raise AssertionError('Unexpected modified file in status: %s' % line)

    @testcase(1172)
    def test_devtool_update_recipe_git(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        testrecipe = 'mtd-utils'
        recipefile = get_bb_var('FILE', testrecipe)
        src_uri = get_bb_var('SRC_URI', testrecipe)
        self.assertIn('git://', src_uri, 'This test expects the %s recipe to be a git recipe' % testrecipe)
        patches = []
        for entry in src_uri.split():
            if entry.startswith('file://') and entry.endswith('.patch'):
                patches.append(entry[7:].split(';')[0])
        self.assertGreater(len(patches), 0, 'The %s recipe does not appear to contain any patches, so this test will not be effective' % testrecipe)
        result = runCmd('git status . --porcelain', cwd=os.path.dirname(recipefile))
        self.assertEqual(result.output.strip(), "", '%s recipe is not clean' % testrecipe)
        # First, modify a recipe
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        self.track_for_cleanup(tempdir)
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        # (don't bother with cleaning the recipe on teardown, we won't be building it)
        result = runCmd('devtool modify %s -x %s' % (testrecipe, tempdir))
        # Check git repo
        self.assertTrue(os.path.isdir(os.path.join(tempdir, '.git')), 'git repository for external source tree not found')
        result = runCmd('git status --porcelain', cwd=tempdir)
        self.assertEqual(result.output.strip(), "", 'Created git repo is not clean')
        result = runCmd('git symbolic-ref HEAD', cwd=tempdir)
        self.assertEqual(result.output.strip(), "refs/heads/devtool", 'Wrong branch in git repo')
        # Add a couple of commits
        # FIXME: this only tests adding, need to also test update and remove
        result = runCmd('echo "# Additional line" >> Makefile', cwd=tempdir)
        result = runCmd('git commit -a -m "Change the Makefile"', cwd=tempdir)
        result = runCmd('echo "A new file" > devtool-new-file', cwd=tempdir)
        result = runCmd('git add devtool-new-file', cwd=tempdir)
        result = runCmd('git commit -m "Add a new file"', cwd=tempdir)
        self.add_command_to_tearDown('cd %s; git checkout %s %s' % (os.path.dirname(recipefile), testrecipe, os.path.basename(recipefile)))
        result = runCmd('devtool update-recipe %s' % testrecipe)
        result = runCmd('git status . --porcelain', cwd=os.path.dirname(recipefile))
        self.assertNotEqual(result.output.strip(), "", '%s recipe should be modified' % testrecipe)
        status = result.output.splitlines()
        for line in status:
            for patch in patches:
                if line.endswith(patch):
                    self.assertEqual(line[:3], ' D ', 'Unexpected status in line: %s' % line)
                    break
            else:
                if re.search('%s_[^_]*.bb$' % testrecipe, line):
                    self.assertEqual(line[:3], ' M ', 'Unexpected status in line: %s' % line)
                else:
                    raise AssertionError('Unexpected modified file in status: %s' % line)
        result = runCmd('git diff %s' % os.path.basename(recipefile), cwd=os.path.dirname(recipefile))
        addlines = ['SRCREV = ".*"', 'SRC_URI = "git://git.infradead.org/mtd-utils.git"']
        srcurilines = src_uri.split()
        srcurilines[0] = 'SRC_URI = "' + srcurilines[0]
        srcurilines.append('"')
        removelines = ['SRCREV = ".*"'] + srcurilines
        for line in result.output.splitlines():
            if line.startswith('+++') or line.startswith('---'):
                continue
            elif line.startswith('+'):
                matched = False
                for item in addlines:
                    if re.match(item, line[1:].strip()):
                        matched = True
                        break
                self.assertTrue(matched, 'Unexpected diff add line: %s' % line)
            elif line.startswith('-'):
                matched = False
                for item in removelines:
                    if re.match(item, line[1:].strip()):
                        matched = True
                        break
                self.assertTrue(matched, 'Unexpected diff remove line: %s' % line)

    @testcase(1170)
    def test_devtool_update_recipe_append(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        testrecipe = 'mdadm'
        recipefile = get_bb_var('FILE', testrecipe)
        src_uri = get_bb_var('SRC_URI', testrecipe)
        self.assertNotIn('git://', src_uri, 'This test expects the %s recipe to NOT be a git recipe' % testrecipe)
        result = runCmd('git status . --porcelain', cwd=os.path.dirname(recipefile))
        self.assertEqual(result.output.strip(), "", '%s recipe is not clean' % testrecipe)
        # First, modify a recipe
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        tempsrcdir = os.path.join(tempdir, 'source')
        templayerdir = os.path.join(tempdir, 'layer')
        self.track_for_cleanup(tempdir)
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        # (don't bother with cleaning the recipe on teardown, we won't be building it)
        result = runCmd('devtool modify %s -x %s' % (testrecipe, tempsrcdir))
        # Check git repo
        self.assertTrue(os.path.isdir(os.path.join(tempsrcdir, '.git')), 'git repository for external source tree not found')
        result = runCmd('git status --porcelain', cwd=tempsrcdir)
        self.assertEqual(result.output.strip(), "", 'Created git repo is not clean')
        result = runCmd('git symbolic-ref HEAD', cwd=tempsrcdir)
        self.assertEqual(result.output.strip(), "refs/heads/devtool", 'Wrong branch in git repo')
        # Add a commit
        result = runCmd("sed 's!\\(#define VERSION\\W*\"[^\"]*\\)\"!\\1-custom\"!' -i ReadMe.c", cwd=tempsrcdir)
        result = runCmd('git commit -a -m "Add our custom version"', cwd=tempsrcdir)
        self.add_command_to_tearDown('cd %s; rm -f %s/*.patch; git checkout .' % (os.path.dirname(recipefile), testrecipe))
        # Create a temporary layer and add it to bblayers.conf
        self._create_temp_layer(templayerdir, True, 'selftestupdaterecipe')
        # Create the bbappend
        result = runCmd('devtool update-recipe %s -a %s' % (testrecipe, templayerdir))
        self.assertNotIn('WARNING:', result.output)
        # Check recipe is still clean
        result = runCmd('git status . --porcelain', cwd=os.path.dirname(recipefile))
        self.assertEqual(result.output.strip(), "", '%s recipe is not clean' % testrecipe)
        # Check bbappend was created
        splitpath = os.path.dirname(recipefile).split(os.sep)
        appenddir = os.path.join(templayerdir, splitpath[-2], splitpath[-1])
        bbappendfile = self._check_bbappend(testrecipe, recipefile, appenddir)
        patchfile = os.path.join(appenddir, testrecipe, '0001-Add-our-custom-version.patch')
        self.assertTrue(os.path.exists(patchfile), 'Patch file not created')

        # Check bbappend contents
        expectedlines = ['FILESEXTRAPATHS_prepend := "${THISDIR}/${PN}:"\n',
                         '\n',
                         'SRC_URI += "file://0001-Add-our-custom-version.patch"\n',
                         '\n']
        with open(bbappendfile, 'r') as f:
            self.assertEqual(expectedlines, f.readlines())

        # Check we can run it again and bbappend isn't modified
        result = runCmd('devtool update-recipe %s -a %s' % (testrecipe, templayerdir))
        with open(bbappendfile, 'r') as f:
            self.assertEqual(expectedlines, f.readlines())
        # Drop new commit and check patch gets deleted
        result = runCmd('git reset HEAD^', cwd=tempsrcdir)
        result = runCmd('devtool update-recipe %s -a %s' % (testrecipe, templayerdir))
        self.assertFalse(os.path.exists(patchfile), 'Patch file not deleted')
        expectedlines2 = ['FILESEXTRAPATHS_prepend := "${THISDIR}/${PN}:"\n',
                         '\n']
        with open(bbappendfile, 'r') as f:
            self.assertEqual(expectedlines2, f.readlines())
        # Put commit back and check we can run it if layer isn't in bblayers.conf
        os.remove(bbappendfile)
        result = runCmd('git commit -a -m "Add our custom version"', cwd=tempsrcdir)
        result = runCmd('bitbake-layers remove-layer %s' % templayerdir, cwd=self.builddir)
        result = runCmd('devtool update-recipe %s -a %s' % (testrecipe, templayerdir))
        self.assertIn('WARNING: Specified layer is not currently enabled in bblayers.conf', result.output)
        self.assertTrue(os.path.exists(patchfile), 'Patch file not created (with disabled layer)')
        with open(bbappendfile, 'r') as f:
            self.assertEqual(expectedlines, f.readlines())
        # Deleting isn't expected to work under these circumstances

    @testcase(1171)
    def test_devtool_update_recipe_append_git(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        testrecipe = 'mtd-utils'
        recipefile = get_bb_var('FILE', testrecipe)
        src_uri = get_bb_var('SRC_URI', testrecipe)
        self.assertIn('git://', src_uri, 'This test expects the %s recipe to be a git recipe' % testrecipe)
        for entry in src_uri.split():
            if entry.startswith('git://'):
                git_uri = entry
                break
        result = runCmd('git status . --porcelain', cwd=os.path.dirname(recipefile))
        self.assertEqual(result.output.strip(), "", '%s recipe is not clean' % testrecipe)
        # First, modify a recipe
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        tempsrcdir = os.path.join(tempdir, 'source')
        templayerdir = os.path.join(tempdir, 'layer')
        self.track_for_cleanup(tempdir)
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        # (don't bother with cleaning the recipe on teardown, we won't be building it)
        result = runCmd('devtool modify %s -x %s' % (testrecipe, tempsrcdir))
        # Check git repo
        self.assertTrue(os.path.isdir(os.path.join(tempsrcdir, '.git')), 'git repository for external source tree not found')
        result = runCmd('git status --porcelain', cwd=tempsrcdir)
        self.assertEqual(result.output.strip(), "", 'Created git repo is not clean')
        result = runCmd('git symbolic-ref HEAD', cwd=tempsrcdir)
        self.assertEqual(result.output.strip(), "refs/heads/devtool", 'Wrong branch in git repo')
        # Add a commit
        result = runCmd('echo "# Additional line" >> Makefile', cwd=tempsrcdir)
        result = runCmd('git commit -a -m "Change the Makefile"', cwd=tempsrcdir)
        self.add_command_to_tearDown('cd %s; rm -f %s/*.patch; git checkout .' % (os.path.dirname(recipefile), testrecipe))
        # Create a temporary layer
        os.makedirs(os.path.join(templayerdir, 'conf'))
        with open(os.path.join(templayerdir, 'conf', 'layer.conf'), 'w') as f:
            f.write('BBPATH .= ":${LAYERDIR}"\n')
            f.write('BBFILES += "${LAYERDIR}/recipes-*/*/*.bbappend"\n')
            f.write('BBFILE_COLLECTIONS += "oeselftesttemplayer"\n')
            f.write('BBFILE_PATTERN_oeselftesttemplayer = "^${LAYERDIR}/"\n')
            f.write('BBFILE_PRIORITY_oeselftesttemplayer = "999"\n')
            f.write('BBFILE_PATTERN_IGNORE_EMPTY_oeselftesttemplayer = "1"\n')
        self.add_command_to_tearDown('bitbake-layers remove-layer %s || true' % templayerdir)
        result = runCmd('bitbake-layers add-layer %s' % templayerdir, cwd=self.builddir)
        # Create the bbappend
        result = runCmd('devtool update-recipe %s -a %s' % (testrecipe, templayerdir))
        self.assertNotIn('WARNING:', result.output)
        # Check recipe is still clean
        result = runCmd('git status . --porcelain', cwd=os.path.dirname(recipefile))
        self.assertEqual(result.output.strip(), "", '%s recipe is not clean' % testrecipe)
        # Check bbappend was created
        splitpath = os.path.dirname(recipefile).split(os.sep)
        appenddir = os.path.join(templayerdir, splitpath[-2], splitpath[-1])
        bbappendfile = self._check_bbappend(testrecipe, recipefile, appenddir)
        self.assertFalse(os.path.exists(os.path.join(appenddir, testrecipe)), 'Patch directory should not be created')

        # Check bbappend contents
        result = runCmd('git rev-parse HEAD', cwd=tempsrcdir)
        expectedlines = ['SRCREV = "%s"\n' % result.output,
                         '\n',
                         'SRC_URI = "%s"\n' % git_uri,
                         '\n']
        with open(bbappendfile, 'r') as f:
            self.assertEqual(expectedlines, f.readlines())

        # Check we can run it again and bbappend isn't modified
        result = runCmd('devtool update-recipe %s -a %s' % (testrecipe, templayerdir))
        with open(bbappendfile, 'r') as f:
            self.assertEqual(expectedlines, f.readlines())
        # Drop new commit and check SRCREV changes
        result = runCmd('git reset HEAD^', cwd=tempsrcdir)
        result = runCmd('devtool update-recipe %s -a %s' % (testrecipe, templayerdir))
        self.assertFalse(os.path.exists(os.path.join(appenddir, testrecipe)), 'Patch directory should not be created')
        result = runCmd('git rev-parse HEAD', cwd=tempsrcdir)
        expectedlines = ['SRCREV = "%s"\n' % result.output,
                         '\n',
                         'SRC_URI = "%s"\n' % git_uri,
                         '\n']
        with open(bbappendfile, 'r') as f:
            self.assertEqual(expectedlines, f.readlines())
        # Put commit back and check we can run it if layer isn't in bblayers.conf
        os.remove(bbappendfile)
        result = runCmd('git commit -a -m "Change the Makefile"', cwd=tempsrcdir)
        result = runCmd('bitbake-layers remove-layer %s' % templayerdir, cwd=self.builddir)
        result = runCmd('devtool update-recipe %s -a %s' % (testrecipe, templayerdir))
        self.assertIn('WARNING: Specified layer is not currently enabled in bblayers.conf', result.output)
        self.assertFalse(os.path.exists(os.path.join(appenddir, testrecipe)), 'Patch directory should not be created')
        result = runCmd('git rev-parse HEAD', cwd=tempsrcdir)
        expectedlines = ['SRCREV = "%s"\n' % result.output,
                         '\n',
                         'SRC_URI = "%s"\n' % git_uri,
                         '\n']
        with open(bbappendfile, 'r') as f:
            self.assertEqual(expectedlines, f.readlines())
        # Deleting isn't expected to work under these circumstances

    @testcase(1163)
    def test_devtool_extract(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        # Try devtool extract
        self.track_for_cleanup(tempdir)
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        result = runCmd('devtool extract remake %s' % tempdir)
        self.assertTrue(os.path.exists(os.path.join(tempdir, 'Makefile.am')), 'Extracted source could not be found')
        self.assertTrue(os.path.isdir(os.path.join(tempdir, '.git')), 'git repository for external source tree not found')

    @testcase(1168)
    def test_devtool_reset_all(self):
        # Check preconditions
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        self.track_for_cleanup(tempdir)
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        testrecipe1 = 'mdadm'
        testrecipe2 = 'cronie'
        result = runCmd('devtool modify -x %s %s' % (testrecipe1, os.path.join(tempdir, testrecipe1)))
        result = runCmd('devtool modify -x %s %s' % (testrecipe2, os.path.join(tempdir, testrecipe2)))
        result = runCmd('devtool build %s' % testrecipe1)
        result = runCmd('devtool build %s' % testrecipe2)
        stampprefix1 = get_bb_var('STAMP', testrecipe1)
        self.assertTrue(stampprefix1, 'Unable to get STAMP value for recipe %s' % testrecipe1)
        stampprefix2 = get_bb_var('STAMP', testrecipe2)
        self.assertTrue(stampprefix2, 'Unable to get STAMP value for recipe %s' % testrecipe2)
        result = runCmd('devtool reset -a')
        self.assertIn(testrecipe1, result.output)
        self.assertIn(testrecipe2, result.output)
        result = runCmd('devtool status')
        self.assertNotIn(testrecipe1, result.output)
        self.assertNotIn(testrecipe2, result.output)
        matches1 = glob.glob(stampprefix1 + '*')
        self.assertFalse(matches1, 'Stamp files exist for recipe %s that should have been cleaned' % testrecipe1)
        matches2 = glob.glob(stampprefix2 + '*')
        self.assertFalse(matches2, 'Stamp files exist for recipe %s that should have been cleaned' % testrecipe2)

    def test_devtool_deploy_target(self):
        # NOTE: Whilst this test would seemingly be better placed as a runtime test,
        # unfortunately the runtime tests run under bitbake and you can't run
        # devtool within bitbake (since devtool needs to run bitbake itself).
        # Additionally we are testing build-time functionality as well, so
        # really this has to be done as an oe-selftest test.
        #
        # Check preconditions
        machine = get_bb_var('MACHINE')
        if not machine.startswith('qemu'):
            self.skipTest('This test only works with qemu machines')
        if not os.path.exists('/etc/runqemu-nosudo'):
            self.skipTest('You must set up tap devices with scripts/runqemu-gen-tapdevs before running this test')
        result = runCmd('PATH="$PATH:/sbin:/usr/sbin" ip tuntap show', ignore_status=True)
        if result.status != 0:
            result = runCmd('PATH="$PATH:/sbin:/usr/sbin" ifconfig -a', ignore_status=True)
            if result.status != 0:
                self.skipTest('Failed to determine if tap devices exist with ifconfig or ip: %s' % result.output)
        for line in result.output.splitlines():
            if line.startswith('tap'):
                break
        else:
            self.skipTest('No tap devices found - you must set up tap devices with scripts/runqemu-gen-tapdevs before running this test')
        workspacedir = os.path.join(self.builddir, 'workspace')
        self.assertTrue(not os.path.exists(workspacedir), 'This test cannot be run with a workspace directory under the build directory')
        # Definitions
        testrecipe = 'mdadm'
        testfile = '/sbin/mdadm'
        testimage = 'oe-selftest-image'
        testcommand = '/sbin/mdadm --help'
        # Build an image to run
        bitbake("%s qemu-native qemu-helper-native" % testimage)
        deploy_dir_image = get_bb_var('DEPLOY_DIR_IMAGE')
        self.add_command_to_tearDown('bitbake -c clean %s' % testimage)
        self.add_command_to_tearDown('rm -f %s/%s*' % (deploy_dir_image, testimage))
        # Clean recipe so the first deploy will fail
        bitbake("%s -c clean" % testrecipe)
        # Try devtool modify
        tempdir = tempfile.mkdtemp(prefix='devtoolqa')
        self.track_for_cleanup(tempdir)
        self.track_for_cleanup(workspacedir)
        self.add_command_to_tearDown('bitbake-layers remove-layer */workspace')
        self.add_command_to_tearDown('bitbake -c clean %s' % testrecipe)
        result = runCmd('devtool modify %s -x %s' % (testrecipe, tempdir))
        # Test that deploy-target at this point fails (properly)
        result = runCmd('devtool deploy-target -n %s root@localhost' % testrecipe, ignore_status=True)
        self.assertNotEqual(result.output, 0, 'devtool deploy-target should have failed, output: %s' % result.output)
        self.assertNotIn(result.output, 'Traceback', 'devtool deploy-target should have failed with a proper error not a traceback, output: %s' % result.output)
        result = runCmd('devtool build %s' % testrecipe)
        # First try a dry-run of deploy-target
        result = runCmd('devtool deploy-target -n %s root@localhost' % testrecipe)
        self.assertIn('  %s' % testfile, result.output)
        # Boot the image
        with runqemu(testimage, self) as qemu:
            # Now really test deploy-target
            result = runCmd('devtool deploy-target -c %s root@%s' % (testrecipe, qemu.ip))
            # Run a test command to see if it was installed properly
            sshargs = '-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no'
            result = runCmd('ssh %s root@%s %s' % (sshargs, qemu.ip, testcommand))
            # Check if it deployed all of the files with the right ownership/perms
            # First look on the host - need to do this under pseudo to get the correct ownership/perms
            installdir = get_bb_var('D', testrecipe)
            fakerootenv = get_bb_var('FAKEROOTENV', testrecipe)
            fakerootcmd = get_bb_var('FAKEROOTCMD', testrecipe)
            result = runCmd('%s %s find . -type f -exec ls -l {} \;' % (fakerootenv, fakerootcmd), cwd=installdir)
            filelist1 = self._process_ls_output(result.output)

            # Now look on the target
            tempdir2 = tempfile.mkdtemp(prefix='devtoolqa')
            self.track_for_cleanup(tempdir2)
            tmpfilelist = os.path.join(tempdir2, 'files.txt')
            with open(tmpfilelist, 'w') as f:
                for line in filelist1:
                    splitline = line.split()
                    f.write(splitline[-1] + '\n')
            result = runCmd('cat %s | ssh -q %s root@%s \'xargs ls -l\'' % (tmpfilelist, sshargs, qemu.ip))
            filelist2 = self._process_ls_output(result.output)
            filelist1.sort(key=lambda item: item.split()[-1])
            filelist2.sort(key=lambda item: item.split()[-1])
            self.assertEqual(filelist1, filelist2)
            # Test undeploy-target
            result = runCmd('devtool undeploy-target -c %s root@%s' % (testrecipe, qemu.ip))
            result = runCmd('ssh %s root@%s %s' % (sshargs, qemu.ip, testcommand), ignore_status=True)
            self.assertNotEqual(result, 0, 'undeploy-target did not remove command as it should have')
