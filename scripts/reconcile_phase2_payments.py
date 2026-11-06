"""Reconcile sealed payment evidence; does not download, train or trade."""
import argparse
import json
from ashare_mvp.phase2_payment_review import reconcile_phase2_payments


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['plans','payments','calendar','review','notices','out','code-changes']:
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    result=reconcile_phase2_payments(**vars(args))
    print(json.dumps({name:result[name] for name in ['events','status_counts','unmatched_accepted_payment_events','model_ready']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
