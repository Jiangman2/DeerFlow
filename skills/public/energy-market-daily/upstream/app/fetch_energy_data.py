"""Freeze validated JKX quotes. The supplied example is read, never executed."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import requests

ROOT = Path(__file__).resolve().parent
SPECS = {
    'LD0221': ('oil', '布伦特原油期货', '美元/桶'),
    'XA0008': ('oil', '布伦特原油现货', '美元/桶'),
    'NY0121': ('oil', 'WTI 原油期货', '美元/桶'),
    'NY0421': ('gas', 'Henry Hub', '美元/百万BTU'),
    'XA0942': ('gas', 'JKM', '美元/百万BTU'),
    'LD1221': ('gas', 'TTF', '美元/百万BTU'),
}

if __name__ == '__main__':
    from fetch_market_history import main
    main()
