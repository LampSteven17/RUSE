"""Traffic-free HLS contract, local player, and duration regressions."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from phase_workflow.loader import CONTRACT_ROOT, WorkflowPlanError, _validate_resource_profile
from phase_workflow.workflows import VIDEO_PAGE, play_video_realtime, _confirm_video_start, _inspect_video_end
from tests.test_phase_workflow_runtime import control_document, load_document


class HlsVideoTests(unittest.TestCase):
    def test_action_success_cannot_override_missing_or_failed_browseruse_judge(self):
        from phase_workflow.brains import _browseruse_completed
        for verdict in (True, False, None, 'true'):
            result = SimpleNamespace(is_done=lambda: True, is_successful=lambda: True,
                                     is_validated=lambda: verdict)
            self.assertEqual(_browseruse_completed(result), verdict is True)
        self.assertFalse(_browseruse_completed(SimpleNamespace(
            is_done=lambda: True, is_successful=lambda: True)))

    def test_mchp_installs_the_system_h264_aac_decoder(self):
        installer = (CONTRACT_ROOT.parents[1] / 'INSTALL_SUP.sh').read_text()
        branch = installer.split('        mchp)')[1].split(';;')[0]
        self.assertIn('libavcodec60', branch)

    def test_same_three_new_resources_validate_for_every_brain_and_profile(self):
        pools = []
        for profile in ('controls-v2', 'feedback-v2'):
            resources = json.loads((CONTRACT_ROOT / 'resource-profiles' / (profile + '.json')).read_text())['resources']
            videos = {k:v for k,v in resources.items() if v['workflow'] == 'VideoViewing'}
            self.assertEqual(set(videos), {'video_hls_mux_bbb', 'video_hls_apple_bipbop', 'video_hls_unified_tos'})
            pools.append(videos)
            for sup in ('scripted-cpu', 'mchp-cpu', 'browseruse-gpu', 'smolagents-gpu'):
                for resource_id in videos:
                    document = control_document(sup)
                    document['resource_profile'] = profile
                    entry = document['schedule'][0]['sequence'][1]
                    entry['resource_id'] = resource_id
                    document['schedule'][0]['sequence'] = [entry]
                    plan = load_document(document)
                    self.assertEqual(dict(plan.windows[0].sequence[0].resource), videos[resource_id])
        self.assertEqual(*pools)

    def test_old_ids_and_invalid_hls_shapes_are_rejected(self):
        document = control_document()
        document['schedule'][0]['sequence'][1]['resource_id'] = 'video_cpp_course'
        with self.assertRaisesRegex(WorkflowPlanError, 'unknown resource'):
            load_document(document)
        valid = dict(workflow='VideoViewing', kind='hls_video', url='https://example.test/test.m3u8', play_seconds=300)
        for changes in ({'url':'http://example.test/v.m3u8'}, {'url':'https:///v'},
                        {'url':'https://user:secret@example.test/v'}, {'play_seconds':299},
                        {'kind':'youtube_video'}, {'video_id':'old'}):
            with self.subTest(changes=changes), self.assertRaises(WorkflowPlanError):
                _validate_resource_profile(dict(schema='phase-resource-profile-v1', id='test', resources={'bad':valid | changes}), 'test')

    def test_ffmpeg_uses_assigned_url_once_and_preserves_checked_duration_command(self):
        task = SimpleNamespace(resource=dict(kind='hls_video', url='https://example.test/assigned.m3u8', play_seconds=300))
        # Progress is the last packet timestamp, not its end; a normal 300s
        # FFmpeg execution can finish with the observed 299.983333s timestamp.
        for output in ('out_time_us=300000000\nprogress=end\n',
                       'out_time_us=299983333\nprogress=end\n'):
            runner = Mock(return_value=SimpleNamespace(stdout=output))
            self.assertTrue(play_video_realtime(task, process_runner=runner))
            runner.assert_called_once()
            command = runner.call_args.args[0]
            self.assertEqual(command[command.index('-i') + 1], task.resource['url'])
            self.assertEqual(command.count(task.resource['url']), 1)
            self.assertEqual(runner.call_args.kwargs['timeout'], 360)
            self.assertIn('-nostdin', command)
            self.assertIn('-re', command)
            self.assertEqual(command[command.index('-t') + 1], '300')
            self.assertTrue(runner.call_args.kwargs['check'])
        for error in (subprocess.TimeoutExpired('ffmpeg',360), subprocess.CalledProcessError(1,'ffmpeg')):
            with self.assertRaises(type(error)):
                play_video_realtime(task, process_runner=Mock(side_effect=error))

    def test_fatal_hls_error_fails_setup_or_final_inspection(self):
        play = Mock()
        with self.assertRaisesRegex(RuntimeError, 'media error'):
            _confirm_video_start(lambda: dict(error='networkError: manifestLoadError', ended=False), play, 30)
        play.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'fragLoadError'):
            _inspect_video_end(lambda: dict(video_present=True, error=None,
                                           player_error='networkError: fragLoadError', time=150, paused=False))

    @unittest.skipUnless(shutil.which('node'), 'JS regression runs with the canary Playwright bundled node')
    def test_packaged_page_native_and_hls_paths_latch_fatal_errors_without_play_or_retry(self):
        script = VIDEO_PAGE.read_text().split('<script>')[1].split('</script>')[0]
        harness = r'''
const assert = require('assert');
for (const native of [true,false]) {
 let loads=[], attached=[], stopped=0, destroyed=0, listener, hide, options;
 const video={canPlayType:()=>native?'maybe':'',play:()=>{throw Error('automatic playback forbidden')}};
 global.window={addEventListener:(event, fn)=>{assert.equal(event,'pagehide');hide=fn}};
 global.document={querySelector:()=>video};
 global.Hls=class {
  static isSupported(){return true}
  static Events={ERROR:'error'};
  static DefaultConfig=Object.fromEntries(['manifestLoadPolicy','playlistLoadPolicy','fragLoadPolicy','keyLoadPolicy','certLoadPolicy','steeringManifestLoadPolicy','interstitialAssetListLoadPolicy'].map(k=>[k,{default:{maxLoadTimeMs:123,timeoutRetry:{},errorRetry:{}}}]));
  constructor(config){options=config}
  on(event,fn){listener=fn}
  loadSource(url){loads.push(url)}
  attachMedia(v){attached.push(v)}
  stopLoad(){stopped++}
  destroy(){destroyed++}
 };
 eval(SCRIPT);
 const url='https://example.test/assigned.m3u8';
 window.loadAssignedHls(url);
 assert.throws(()=>window.loadAssignedHls(url));
 if(native){assert.equal(video.src,url);assert.equal(loads.length,0)}
 else {
  assert.deepEqual(loads,[url]);assert.deepEqual(attached,[video]);
  for(const policy of Object.values(options)){assert.equal(policy.default.timeoutRetry,null);assert.equal(policy.default.errorRetry,null);assert.equal(policy.default.maxLoadTimeMs,123)}
  listener(null,{fatal:false,type:'mediaError',details:'nonfatal'});assert.equal(window.ruseVideoError,null);
  listener(null,{fatal:true,type:'networkError',details:'fragLoadError'});
  assert.equal(window.ruseVideoError,'networkError: fragLoadError');assert.equal(stopped,1);
  hide();assert.equal(destroyed,1);
 }
}
'''.replace('SCRIPT', json.dumps(script))
        subprocess.run(['node','-e',harness], check=True, capture_output=True, text=True, timeout=10)
        self.assertTrue((VIDEO_PAGE.parent / 'hls-1.7.2.min.js').is_file())
        self.assertNotIn('https://', VIDEO_PAGE.read_text())


if __name__ == '__main__':
    unittest.main()
