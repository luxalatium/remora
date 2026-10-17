"""Bash environment forwarding for network selectors; no SSH or scheduler."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
KEYS=('REMORA_ETH_INTERFACES','REMORA_IB_PORTS','REMORA_OPA_PORTS')


class NetworkEnvironment(unittest.TestCase):
    def run_case(self,values,full_init=False):
        with tempfile.TemporaryDirectory(prefix='network-env-') as tmp:
            directory=Path(tmp)
            marker=directory/'SHOULD_NOT_EXIST'
            values={k:v.replace('{marker}',str(marker)) for k,v in values.items()}
            env=dict(os.environ)
            for key in KEYS:env.pop(key,None)
            env.update(values)
            setup='''source "$1"
if [[ $3 == full ]]; then
    REMORA_JOB_ID=networkenv
    unset REMORA_TMPDIR
    remora_set_active_modules() { REMORA_ACTIVE_MODULES_OUTDIRS=(NETWORK); }
    init_folders || exit
    file=$REMORA_OUTDIR/remora_env.txt
else
    file=$2/environment.sh
    write_resource_environment "$file" || exit
fi
# A clean worker environment can only obtain selectors from the saved file.
env -i PATH="$PATH" bash -c 'source "$1"; for k in REMORA_ETH_INTERFACES REMORA_IB_PORTS REMORA_OPA_PORTS; do if [[ ${!k+x} ]]; then printf "%s\\0" "${!k}"; else printf "UNSET\\0"; fi; done' worker "$file"
'''
            r=subprocess.run(['bash','-c',setup,'network-env',str(ROOT/'src/aux/extra'),str(directory),'full' if full_init else 'helper'],env=env,cwd=directory,capture_output=True,timeout=5)
            self.assertEqual(r.returncode,0,r.stderr)
            actual=r.stdout.decode().split('\0')[:-1]
            self.assertEqual(actual,[values.get(k,'UNSET') for k in KEYS])
            self.assertFalse(marker.exists(),'saved selector evaluated executable input')

    def test_selectors_survive_actual_folder_initialization_and_clean_worker(self):
        self.run_case(dict(zip(KEYS,['eth0,eth1','mlx5_0/1,mlx5_1/1','hfi1_0/2'])),full_init=True)

    def test_empty_and_unset_are_distinct(self):
        self.run_case({'REMORA_ETH_INTERFACES':''})

    def test_malformed_selectors_remain_literal_data(self):
        self.run_case({'REMORA_ETH_INTERFACES':'$(touch {marker}); echo bad','REMORA_IB_PORTS':'a"b\n$HOME','REMORA_OPA_PORTS':"x'y; touch {marker}"})


if __name__=='__main__':unittest.main()
