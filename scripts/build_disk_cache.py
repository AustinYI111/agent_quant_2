"""Create immutable quote or reference caches in an explicit output directory."""
import argparse,json
import pandas as pd
from ashare_mvp.disk_market import build_quote_store,build_reference_audit


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    quotes=commands.add_parser('quotes',help='cache raw issuer-sorted CSV once')
    for name in ['raw','calendar','out']:quotes.add_argument('--'+name,required=True)
    quotes.add_argument('--chunksize',type=int,default=25000)
    references=commands.add_parser('references',help='audit reviewed actions without duplicating quotes')
    for name in ['store','raw','actions','out']:references.add_argument('--'+name,required=True)
    references.add_argument('--tick',type=float,default=.01)
    args=parser.parse_args()
    if args.command=='quotes':
        calendar=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(args.calendar).date))
        result=build_quote_store(args.raw,calendar,args.out,args.chunksize)
    else:result=build_reference_audit(args.store,args.raw,args.actions,args.out,args.tick)
    print(json.dumps(dict(status=result['status'],rows=result['rows'],issuers=result['issuers'])),flush=True)


if __name__=='__main__':main()
