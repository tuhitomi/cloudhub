"""Interrupted evidence must never resume a different owner or a retired source."""
import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aws_lifecycle import validate_created_source_resume

class LifecycleResumeTests(unittest.TestCase):
    def setUp(self):
        self.record={'mode':'AWS_REAL','account_id':'123456789012','region':'ap-southeast-1','template_id':'T05',
            'result':'FAILED','completed_cycles':0,'cycles':[{'number':1,'create_operation':{'environment_id':'env-source'},
            'source_environment':{'environment_id':'env-source','owner_id':'alice','template_id':'T05'}}]}
    def check(self,record=None,owner='alice'):
        return validate_created_source_resume(record or self.record,'T05','123456789012','ap-southeast-1','env-source',owner)
    def test_foreign_owner_account_or_source_cannot_resume(self):
        with self.assertRaises(ValueError):self.check(owner='bob')
        for field,value in [('account_id','999999999999'),('region','us-east-1'),('template_id','T01')]:
            altered=copy.deepcopy(self.record);altered[field]=value
            with self.assertRaises(ValueError):self.check(altered)
        altered=copy.deepcopy(self.record);altered['cycles'][0]['source_environment']['environment_id']='env-other'
        with self.assertRaises(ValueError):self.check(altered)
    def test_completed_or_backed_up_sources_require_separate_review(self):
        for field in ['backup_operation','destroy_operation','restore_operation','source_cleanup']:
            altered=copy.deepcopy(self.record);altered['cycles'][0][field]={}
            with self.assertRaises(ValueError):self.check(altered)
        for result in ['PASS','UNKNOWN']:
            altered=copy.deepcopy(self.record);altered['result']=result
            with self.assertRaises(ValueError):self.check(altered)
        altered=copy.deepcopy(self.record);altered['progress']={'operation':{'phase':'BACKUP'}}
        with self.assertRaises(ValueError):self.check(altered)
    def test_same_owned_prebackup_source_preserves_original_cycle(self):
        self.assertIs(self.check(),self.record['cycles'][0])

if __name__=='__main__':unittest.main()
