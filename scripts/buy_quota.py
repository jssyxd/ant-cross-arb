#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
buy_quota.py - 正确签署并向 Hyperliquid L1 购买请求配额
价格: 0.0005 USDC / 请求 (买 20,000 次 = 10 USDC)
"""
import time
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from hyperliquid.utils.signing import sign_l1_action, get_timestamp_ms

WALLET_PK = "<HL_AGENT_PRIVATE_KEY_FROM_ENV>"
ACCOUNT_ADDR = "0xAF3E93d42b56d517F055dDF64bcB4531c0B1E815"
WEIGHT_TO_BUY = 20000  # 购买 20,000 次配额 (花费 10.0 USDC)

def main():
    wallet = Account.from_key(WALLET_PK)
    exchange = Exchange(
        wallet=wallet,
        base_url=constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDR
    )

    action = {
        "type": "reserveRequestWeight",
        "weight": WEIGHT_TO_BUY
    }

    nonce = get_timestamp_ms()
    signature = sign_l1_action(
        wallet=wallet,
        action=action,
        active_pool=None,
        nonce=nonce,
        expires_after=None,
        is_mainnet=True
    )

    print(f"正在为 {ACCOUNT_ADDR} 购买 {WEIGHT_TO_BUY} 次 API 请求配额 (成本: {WEIGHT_TO_BUY * 0.0005} USDC)...")
    res = exchange._post_action(action, signature, nonce)
    print("返回结果:", res)

if __name__ == "__main__":
    main()
