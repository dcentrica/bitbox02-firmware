// Copyright 2020 Shift Crypto AG
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

use super::pb;
use pb::BtcCoin;

use util::bip32::HARDENED;

/// Parameters for BTC-like coins. See also:
/// https://en.bitcoin.it/wiki/List_of_address_prefixes
pub struct Params {
    /// https://github.com/satoshilabs/slips/blob/master/slip-0044.md
    pub bip44_coin: u32,
    pub base58_version_p2pkh: u8,
    pub base58_version_p2sh: u8,
    pub bech32_hrp: &'static str,
    pub name: &'static str,
    pub unit: &'static str,
    pub rbf_support: bool,
}

/// Keep these in sync with btc_params.c.

const PARAMS_BTC: Params = Params {
    bip44_coin: 0 + HARDENED,
    base58_version_p2pkh: 0x00, // starts with 1
    base58_version_p2sh: 0x05,  // starts with 3
    bech32_hrp: "bc",
    name: "Bitcoin",
    unit: "BTC",
    rbf_support: true,
};

const PARAMS_TBTC: Params = Params {
    bip44_coin: 1 + HARDENED,
    base58_version_p2pkh: 0x6f, // starts with m or n
    base58_version_p2sh: 0xc4,  // starts with 2
    bech32_hrp: "tb",
    name: "BTC Testnet",
    unit: "TBTC",
    rbf_support: true,
};

const PARAMS_LTC: Params = Params {
    bip44_coin: 2 + HARDENED,
    base58_version_p2pkh: 0x30, // starts with L
    base58_version_p2sh: 0x32,  // starts with M
    bech32_hrp: "ltc",
    name: "Litecoin",
    unit: "LTC",
    rbf_support: false,
};

const PARAMS_TLTC: Params = Params {
    bip44_coin: 1 + HARDENED,
    base58_version_p2pkh: 0x6f, // starts with m or n
    base58_version_p2sh: 0xc4,  // starts with 2
    bech32_hrp: "tltc",
    name: "LTC Testnet",
    unit: "TLTC",
    rbf_support: false,
};

pub fn get(coin: BtcCoin) -> &'static Params {
    use BtcCoin::*;
    match coin {
        Btc => &PARAMS_BTC,
        Tbtc => &PARAMS_TBTC,
        Ltc => &PARAMS_LTC,
        Tltc => &PARAMS_TLTC,
    }
}
