# Copyright 2019 Shift Cryptosecurity AG
# Copyright 2020 Shift Crypto AG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""BitBox02"""


import os
import sys
import time
from datetime import datetime
from typing import Optional, List, Dict, Tuple, Any, Generator, Union, Sequence
from typing_extensions import TypedDict

import semver

from bitbox02.communication import (
    BitBoxCommonAPI,
    Bitbox02Exception,
    ERR_GENERIC,
    ERR_DUPLICATE_ENTRY,
)

from .secp256k1 import antiklepto_host_commit, antiklepto_verify

try:
    from bitbox02.communication.generated import hww_pb2 as hww
    from bitbox02.communication.generated import eth_pb2 as eth
    from bitbox02.communication.generated import btc_pb2 as btc
    from bitbox02.communication.generated import mnemonic_pb2 as mnemonic
    from bitbox02.communication.generated import bitbox02_system_pb2 as bitbox02_system
    from bitbox02.communication.generated import backup_commands_pb2 as backup
    from bitbox02.communication.generated import common_pb2 as common
    from bitbox02.communication.generated import keystore_pb2 as keystore
    from bitbox02.communication.generated import antiklepto_pb2 as antiklepto

    # pylint: disable=unused-import
    # We export it in __init__.py
    from bitbox02.communication.generated import system_pb2 as system
except ModuleNotFoundError:
    print("Run `make py` to generate the protobuf messages")
    sys.exit()

try:
    # Optional rlp dependency only needed to sign ethereum transactions.
    # pylint: disable=import-error
    import rlp
except ModuleNotFoundError:
    pass

HARDENED = 0x80000000

Backup = Tuple[str, str, datetime]


class DuplicateEntryException(Exception):
    pass


class BTCPrevTxInputType(TypedDict):
    prev_out_hash: bytes
    prev_out_index: int
    signature_script: bytes
    sequence: int


class BTCPrevTxOutputType(TypedDict):
    value: int
    pubkey_script: bytes


class BTCPrevTxType(TypedDict):
    version: int
    locktime: int
    inputs: Sequence[BTCPrevTxInputType]
    outputs: Sequence[BTCPrevTxOutputType]


class BTCInputType(TypedDict):
    prev_out_hash: bytes
    prev_out_index: int
    prev_out_value: int
    sequence: int
    keypath: Sequence[int]
    script_config_index: int
    prev_tx: BTCPrevTxType


class BTCOutputInternal:
    # TODO: Use NamedTuple, but not playing well with protobuf types.

    def __init__(self, keypath: Sequence[int], value: int, script_config_index: int):
        """
        keypath: keypath to the change output.
        """
        self.keypath = keypath
        self.value = value
        self.script_config_index = script_config_index


class BTCOutputExternal:
    # TODO: Use NamedTuple, but not playing well with protobuf types.

    def __init__(self, output_type: btc.BTCOutputType, output_hash: bytes, value: int):
        self.type = output_type
        self.hash = output_hash
        self.value = value


BTCOutputType = Union[BTCOutputInternal, BTCOutputExternal]


class BitBox02(BitBoxCommonAPI):
    """Class to communicate with a BitBox02"""

    # pylint: disable=too-many-public-methods

    def device_info(self) -> Dict[str, Any]:
        # pylint: disable=no-member
        request = hww.Request()
        device_info_request = bitbox02_system.DeviceInfoRequest()
        request.device_info.CopyFrom(device_info_request)
        response = self._msg_query(request, expected_response="device_info")
        result = {
            "name": response.device_info.name,
            "version": response.device_info.version,
            "initialized": response.device_info.initialized,
            "mnemonic_passphrase_enabled": response.device_info.mnemonic_passphrase_enabled,
            "monotonic_increments_remaining": response.device_info.monotonic_increments_remaining,
        }
        if self.version >= semver.VersionInfo(9, 6, 0):
            result["securechip_model"] = response.device_info.securechip_model

        return result

    def set_device_name(self, device_name: str) -> None:
        # pylint: disable=no-member
        request = hww.Request()
        request.device_name.name = device_name
        self._msg_query(request, expected_response="success")

    def set_password(self, entropy_size: int = 32) -> bool:
        """
        Returns True if the user entered the password correctly (passwords match).
        Returns False otherwise. Entropy size determines the seed size in bytes; must be 16 or 32.
        """
        assert entropy_size in (16, 32)
        if entropy_size == 16:
            self._require_atleast(semver.VersionInfo(9, 6, 0))

        # pylint: disable=no-member
        request = hww.Request()
        request.set_password.entropy = os.urandom(entropy_size)
        try:
            self._msg_query(request, expected_response="success")
        except Bitbox02Exception as err:
            if err.code == ERR_GENERIC:
                return False
            raise
        return True

    def create_backup(self) -> bool:
        """
        Returns True if the backup was created successfully.
        Returns False otherwise.
        """
        # pylint: disable=no-member
        request = hww.Request()
        request.create_backup.timestamp = int(time.time())
        request.create_backup.timezone_offset = time.localtime().tm_gmtoff
        try:
            self._msg_query(request, expected_response="success")
        except Bitbox02Exception as err:
            if err.code == ERR_GENERIC:
                return False
            raise
        return True

    def list_backups(self) -> Generator[Backup, None, None]:
        """
        Returns a pair of id and timestamp's strings that identify the backups.
        """
        # pylint: disable=no-member
        self.insert_sdcard()
        request = hww.Request()
        request.list_backups.CopyFrom(backup.ListBackupsRequest())
        response = self._msg_query(request, expected_response="list_backups")
        for info in response.list_backups.info:
            utcdate = datetime.utcfromtimestamp(info.timestamp)
            yield (info.id, info.name, utcdate)

    def restore_backup(self, backup_id: str) -> bool:
        """
        Sends a restore API call to the BitBox.
        """
        # pylint: disable=no-member
        request = hww.Request()
        request.restore_backup.id = backup_id
        request.restore_backup.timestamp = int(time.time())
        request.restore_backup.timezone_offset = time.localtime().tm_gmtoff
        try:
            self._msg_query(request, expected_response="success")
        except Bitbox02Exception as err:
            if err.code == ERR_GENERIC:
                return False
            raise
        return True

    def check_backup(self, silent: bool = False) -> Optional[str]:
        """
        Sends a check backup API call to the BitBox.
        Returns the backup ID if the backup was found and can be restored.
        Otherwise, returns None. If silent is True, the result won't be shown on the device screen.
        """
        # pylint: disable=no-member
        self.insert_sdcard()
        request = hww.Request()
        request.check_backup.CopyFrom(backup.CheckBackupRequest(silent=silent))
        try:
            response = self._msg_query(request, expected_response="check_backup")
        except Bitbox02Exception as err:
            if err.code == ERR_GENERIC:
                return None
            raise
        return response.check_backup.id

    def show_mnemonic(self) -> None:
        """
        Returns True if mnemonic was successfully shown and confirmed.
        Raises a Bitbox02Exception on failure.
        """
        # pylint: disable=no-member
        request = hww.Request()
        request.show_mnemonic.CopyFrom(mnemonic.ShowMnemonicRequest())
        self._msg_query(request, expected_response="success")

    def _btc_msg_query(
        self, btc_request: btc.BTCRequest, expected_response: Optional[str] = None
    ) -> btc.BTCResponse:
        """
        Same as _msg_query, but one nesting deeper for bitcoin messages.
        """
        # pylint: disable=no-member
        request = hww.Request()
        request.btc.CopyFrom(btc_request)
        btc_response = self._msg_query(request, expected_response="btc").btc
        if (
            expected_response is not None
            and btc_response.WhichOneof("response") != expected_response
        ):
            raise Exception(
                "Unexpected response: {}, expected: {}".format(
                    btc_response.WhichOneof("response"), expected_response
                )
            )
        return btc_response

    def btc_xpub(
        self,
        keypath: Sequence[int],
        coin: btc.BTCCoin = btc.BTC,
        xpub_type: btc.BTCPubRequest.XPubType = btc.BTCPubRequest.XPUB,
        display: bool = True,
    ) -> str:
        """
        keypath is a list of child derivation numbers.
        e.g. m/44'/0'/1' corresponds to [44+HARDENED, 0+HARDENED, 1+HARDENED].
        """
        # pylint: disable=no-member,too-many-arguments
        request = hww.Request()
        request.btc_pub.CopyFrom(
            btc.BTCPubRequest(coin=coin, keypath=keypath, xpub_type=xpub_type, display=display)
        )
        return self._msg_query(request).pub.pub

    def btc_address(
        self,
        keypath: Sequence[int],
        coin: btc.BTCCoin = btc.BTC,
        script_config: btc.BTCScriptConfig = btc.BTCScriptConfig(
            simple_type=btc.BTCScriptConfig.P2WPKH
        ),
        display: bool = True,
    ) -> str:
        """
        keypath is a list of child derivation numbers.
        e.g. m/44'/0'/1'/5/10 corresponds to [44+HARDENED, 0+HARDENED, 1+HARDENED, 5, 10].
        """
        # pylint: disable=no-member,too-many-arguments
        request = hww.Request()
        request.btc_pub.CopyFrom(
            btc.BTCPubRequest(
                coin=coin, keypath=keypath, script_config=script_config, display=display
            )
        )
        return self._msg_query(request).pub.pub

    def btc_is_script_config_registered(
        self, coin: btc.BTCCoin, script_config: btc.BTCScriptConfig, keypath: Sequence[int]
    ) -> bool:
        """
        Returns True if the script config / account is already registered.
        """
        # pylint: disable=no-member
        request = btc.BTCRequest()
        request.is_script_config_registered.CopyFrom(
            btc.BTCIsScriptConfigRegisteredRequest(
                registration=btc.BTCScriptConfigRegistration(
                    coin=coin, script_config=script_config, keypath=keypath
                )
            )
        )
        return self._btc_msg_query(
            request, expected_response="is_script_config_registered"
        ).is_script_config_registered.is_registered

    def btc_register_script_config(
        self,
        coin: btc.BTCCoin,
        script_config: btc.BTCScriptConfig,
        keypath: Sequence[int],
        name: str,
        xpub_type: btc.BTCRegisterScriptConfigRequest.XPubType = btc.BTCRegisterScriptConfigRequest.XPubType.AUTO_ELECTRUM,
    ) -> None:
        """
        Raises Bitbox02Exception with ERR_USER_ABORT on user abort.
        If name is the empty string, it will be prompted on the device.
        """
        # pylint: disable=no-member,too-many-arguments

        if name == "":
            # prompt on device only available since v9.3.0
            self._require_atleast(semver.VersionInfo(9, 3, 0))

        assert len(name) <= 30

        # pylint: disable=no-member
        request = btc.BTCRequest()
        request.register_script_config.CopyFrom(
            btc.BTCRegisterScriptConfigRequest(
                registration=btc.BTCScriptConfigRegistration(
                    coin=coin, script_config=script_config, keypath=keypath
                ),
                name=name,
                xpub_type=xpub_type,
            )
        )
        try:
            self._btc_msg_query(request, expected_response="success")
        except Bitbox02Exception as err:
            if err.code == ERR_DUPLICATE_ENTRY:
                raise DuplicateEntryException(
                    "A multisig account configuration with this name already exists.\n"
                    "Choose another name."
                )
            raise

    # pylint: disable=too-many-arguments
    def btc_sign(
        self,
        coin: btc.BTCCoin,
        script_configs: Sequence[btc.BTCScriptConfigWithKeypath],
        inputs: Sequence[BTCInputType],
        outputs: Sequence[BTCOutputType],
        version: int = 1,
        locktime: int = 0,
    ) -> Sequence[Tuple[int, bytes]]:
        """
        coin: the first element of all provided keypaths must match the coin:
        - BTC: 0 + HARDENED
        - Testnets: 1 + HARDENED
        - LTC: 2 + HARDENED
        script_type: type of all inputs and change outputs. The first element of all provided
        keypaths must match this type:
        - SCRIPT_P2PKH: 44 + HARDENED
        - SCRIPT_P2WPKH_P2SH: 49 + HARDENED
        - SCRIPT_P2WPKH: 84 + HARDENED
        bip44_account: Starting at (0 + HARDENED), must be the third element of all provided
        keypaths.
        inputs: transaction inputs.
        outputs: transaction outputs. Can be an external output
        (BTCOutputExternal) or an internal output for change (BTCOutputInternal).
        version, locktime: reserved for future use.
        Returns: list of (input index, signature) tuples.
        Raises Bitbox02Exception with ERR_USER_ABORT on user abort.
        """
        # pylint: disable=no-member,too-many-locals,too-many-branches,too-many-statements

        # Reserved for future use.
        assert version in (1, 2)

        supports_antiklepto = self.version >= semver.VersionInfo(9, 4, 0)

        sigs: List[Tuple[int, bytes]] = []

        # Init request
        request = hww.Request()
        request.btc_sign_init.CopyFrom(
            btc.BTCSignInitRequest(
                coin=coin,
                script_configs=script_configs,
                version=version,
                num_inputs=len(inputs),
                num_outputs=len(outputs),
                locktime=locktime,
            )
        )
        next_response = self._msg_query(request, expected_response="btc_sign_next").btc_sign_next

        is_inputs_pass2 = False
        while True:
            if next_response.type == btc.BTCSignNextResponse.INPUT:
                input_index = next_response.index
                tx_input = inputs[input_index]

                request = hww.Request()
                request.btc_sign_input.CopyFrom(
                    btc.BTCSignInputRequest(
                        prevOutHash=tx_input["prev_out_hash"],
                        prevOutIndex=tx_input["prev_out_index"],
                        prevOutValue=tx_input["prev_out_value"],
                        sequence=tx_input["sequence"],
                        keypath=tx_input["keypath"],
                        script_config_index=tx_input["script_config_index"],
                    )
                )
                if supports_antiklepto and is_inputs_pass2:
                    host_nonce = os.urandom(32)
                    request.btc_sign_input.host_nonce_commitment.commitment = antiklepto_host_commit(
                        host_nonce
                    )

                next_response = self._msg_query(
                    request, expected_response="btc_sign_next"
                ).btc_sign_next

                if supports_antiklepto and is_inputs_pass2:
                    assert next_response.type == btc.BTCSignNextResponse.HOST_NONCE
                    assert next_response.HasField("anti_klepto_signer_commitment")
                    signer_commitment = next_response.anti_klepto_signer_commitment.commitment

                    btc_request = btc.BTCRequest()
                    btc_request.antiklepto_signature.CopyFrom(
                        antiklepto.AntiKleptoSignatureRequest(host_nonce=host_nonce)
                    )
                    next_response = self._btc_msg_query(
                        btc_request, expected_response="sign_next"
                    ).sign_next

                    if self.debug:
                        print(
                            f"For input {input_index}, the host contributed the nonce {host_nonce.hex()}"
                        )

                    assert next_response.has_signature
                    antiklepto_verify(host_nonce, signer_commitment, next_response.signature)

                    if self.debug:
                        print(f"Antiklepto nonce verification PASSED for input {input_index}")

                if is_inputs_pass2:
                    assert next_response.has_signature
                    sigs.append((input_index, next_response.signature))

                if input_index == len(inputs) - 1:
                    is_inputs_pass2 = True

            elif next_response.type == btc.BTCSignNextResponse.PREVTX_INIT:
                prevtx = inputs[next_response.index]["prev_tx"]
                btc_request = btc.BTCRequest()
                btc_request.prevtx_init.CopyFrom(
                    btc.BTCPrevTxInitRequest(
                        version=prevtx["version"],
                        num_inputs=len(prevtx["inputs"]),
                        num_outputs=len(prevtx["outputs"]),
                        locktime=prevtx["locktime"],
                    )
                )
                next_response = self._btc_msg_query(
                    btc_request, expected_response="sign_next"
                ).sign_next
            elif next_response.type == btc.BTCSignNextResponse.PREVTX_INPUT:
                prevtx_input = inputs[next_response.index]["prev_tx"]["inputs"][
                    next_response.prev_index
                ]
                btc_request = btc.BTCRequest()
                btc_request.prevtx_input.CopyFrom(
                    btc.BTCPrevTxInputRequest(
                        prev_out_hash=prevtx_input["prev_out_hash"],
                        prev_out_index=prevtx_input["prev_out_index"],
                        signature_script=prevtx_input["signature_script"],
                        sequence=prevtx_input["sequence"],
                    )
                )
                next_response = self._btc_msg_query(
                    btc_request, expected_response="sign_next"
                ).sign_next
            elif next_response.type == btc.BTCSignNextResponse.PREVTX_OUTPUT:
                prevtx_output = inputs[next_response.index]["prev_tx"]["outputs"][
                    next_response.prev_index
                ]
                btc_request = btc.BTCRequest()
                btc_request.prevtx_output.CopyFrom(
                    btc.BTCPrevTxOutputRequest(
                        value=prevtx_output["value"], pubkey_script=prevtx_output["pubkey_script"]
                    )
                )
                next_response = self._btc_msg_query(
                    btc_request, expected_response="sign_next"
                ).sign_next
            elif next_response.type == btc.BTCSignNextResponse.OUTPUT:
                output_index = next_response.index
                tx_output = outputs[output_index]

                request = hww.Request()
                if isinstance(tx_output, BTCOutputInternal):
                    request.btc_sign_output.CopyFrom(
                        btc.BTCSignOutputRequest(
                            ours=True,
                            value=tx_output.value,
                            keypath=tx_output.keypath,
                            script_config_index=tx_output.script_config_index,
                        )
                    )
                elif isinstance(tx_output, BTCOutputExternal):
                    request.btc_sign_output.CopyFrom(
                        btc.BTCSignOutputRequest(
                            ours=False,
                            type=tx_output.type,
                            hash=tx_output.hash,
                            value=tx_output.value,
                        )
                    )
                next_response = self._msg_query(
                    request, expected_response="btc_sign_next"
                ).btc_sign_next
            elif next_response.type == btc.BTCSignNextResponse.DONE:
                break
            else:
                raise Exception("unexpected response")
        return sigs

    def btc_sign_msg(
        self, coin: btc.BTCCoin, script_config: btc.BTCScriptConfigWithKeypath, msg: bytes
    ) -> Tuple[bytes, int, bytes]:
        """
        Returns a 64 byte sig, the recoverable id, and a 65 byte signature containing
        the recid, compatible with Electrum.
        """
        # pylint: disable=no-member,line-too-long

        self._require_atleast(semver.VersionInfo(9, 2, 0))

        request = btc.BTCRequest()
        request.sign_message.CopyFrom(
            btc.BTCSignMessageRequest(coin=coin, script_config=script_config, msg=msg)
        )

        supports_antiklepto = self.version >= semver.VersionInfo(9, 5, 0)
        if supports_antiklepto:
            host_nonce = os.urandom(32)

            request.sign_message.host_nonce_commitment.commitment = antiklepto_host_commit(
                host_nonce
            )
            signer_commitment = self._btc_msg_query(
                request, expected_response="antiklepto_signer_commitment"
            ).antiklepto_signer_commitment.commitment

            request = btc.BTCRequest()
            request.antiklepto_signature.CopyFrom(
                antiklepto.AntiKleptoSignatureRequest(host_nonce=host_nonce)
            )

            signature = self._btc_msg_query(
                request, expected_response="sign_message"
            ).sign_message.signature
            antiklepto_verify(host_nonce, signer_commitment, signature[:64])

            if self.debug:
                print(f"Antiklepto nonce verification PASSED")

        else:
            signature = self._btc_msg_query(
                request, expected_response="sign_message"
            ).sign_message.signature

        sig, recid = signature[:64], signature[64]

        # See https://github.com/spesmilo/electrum/blob/84dc181b6e7bb20e88ef6b98fb8925c5f645a765/electrum/ecc.py#L521-L523
        compressed = 4  # BitBox02 uses only compressed pubkeys
        electrum_sig65 = bytes([27 + compressed + recid]) + sig

        return (sig, recid, electrum_sig65)

    def check_sdcard(self) -> bool:
        # pylint: disable=no-member
        request = hww.Request()
        request.check_sdcard.CopyFrom(bitbox02_system.CheckSDCardRequest())
        response = self._msg_query(request, expected_response="check_sdcard")
        return response.check_sdcard.inserted

    def insert_sdcard(self) -> None:
        # pylint: disable=no-member
        request = hww.Request()
        request.insert_remove_sdcard.CopyFrom(
            bitbox02_system.InsertRemoveSDCardRequest(
                action=bitbox02_system.InsertRemoveSDCardRequest.INSERT_CARD
            )
        )
        self._msg_query(request, expected_response="success")

    def remove_sdcard(self) -> None:
        # pylint: disable=no-member
        request = hww.Request()
        request.insert_remove_sdcard.CopyFrom(
            bitbox02_system.InsertRemoveSDCardRequest(
                action=bitbox02_system.InsertRemoveSDCardRequest.REMOVE_CARD
            )
        )
        self._msg_query(request, expected_response="success")

    def root_fingerprint(self) -> bytes:
        """
        Get the root fingerprint from the bitbox02
        """
        # pylint: disable=no-member
        request = hww.Request()
        request.fingerprint.CopyFrom(common.RootFingerprintRequest())
        response = self._msg_query(request, expected_response="fingerprint")
        return response.fingerprint.fingerprint

    def electrum_encryption_key(self, keypath: Sequence[int]) -> str:
        """
        This call fetches the xpub used for the electrum wallet encryption
        """
        # pylint: disable=no-member
        request = hww.Request()
        request.electrum_encryption_key.CopyFrom(
            keystore.ElectrumEncryptionKeyRequest(keypath=keypath)
        )
        return self._msg_query(request).electrum_encryption_key.key

    def enable_mnemonic_passphrase(self) -> None:
        """
        Enable the bip39 passphrase.
        """
        # pylint: disable=no-member
        request = hww.Request()
        request.set_mnemonic_passphrase_enabled.enabled = True
        self._msg_query(request, expected_response="success")

    def disable_mnemonic_passphrase(self) -> None:
        """
        Disable the bip39 passphrase.
        """
        # pylint: disable=no-member
        request = hww.Request()
        request.set_mnemonic_passphrase_enabled.enabled = False
        self._msg_query(request, expected_response="success")

    def _eth_msg_query(
        self, eth_request: eth.ETHRequest, expected_response: Optional[str] = None
    ) -> eth.ETHResponse:
        """
        Same as _msg_query, but one nesting deeper for ethereum messages.
        """
        # pylint: disable=no-member
        request = hww.Request()
        request.eth.CopyFrom(eth_request)
        eth_response = self._msg_query(request, expected_response="eth").eth
        if (
            expected_response is not None
            and eth_response.WhichOneof("response") != expected_response
        ):
            raise Exception(
                "Unexpected response: {}, expected: {}".format(
                    eth_response.WhichOneof("response"), expected_response
                )
            )
        return eth_response

    def eth_pub(
        self,
        keypath: Sequence[int],
        coin: eth.ETHCoin = eth.ETH,
        output_type: eth.ETHPubRequest.OutputType = eth.ETHPubRequest.ADDRESS,
        display: bool = True,
        contract_address: bytes = b"",
    ) -> str:
        """
        keypath is a list of child derivation numbers.
        e.g. m/44'/60'/0'/0/5 corresponds to [44+HARDENED, 60+HARDENED, 0+HARDENED, 0, 5].
        """
        # pylint: disable=no-member
        request = eth.ETHRequest()
        request.pub.CopyFrom(
            eth.ETHPubRequest(
                coin=coin,
                keypath=keypath,
                output_type=output_type,
                display=display,
                contract_address=contract_address,
            )
        )
        return self._eth_msg_query(request, expected_response="pub").pub.pub

    def eth_sign(
        self, transaction: bytes, keypath: Sequence[int], coin: eth.ETHCoin = eth.ETH
    ) -> bytes:
        """
        transaction should be given as a full rlp encoded eth transaction.
        """
        nonce, gas_price, gas_limit, recipient, value, data, _, _, _ = rlp.decode(transaction)
        request = eth.ETHRequest()
        # pylint: disable=no-member
        request.sign.CopyFrom(
            eth.ETHSignRequest(
                coin=coin,
                keypath=keypath,
                nonce=nonce,
                gas_price=gas_price,
                gas_limit=gas_limit,
                recipient=recipient,
                value=value,
                data=data,
            )
        )

        supports_antiklepto = self.version >= semver.VersionInfo(9, 5, 0)
        if supports_antiklepto:
            host_nonce = os.urandom(32)

            request.sign.host_nonce_commitment.commitment = antiklepto_host_commit(host_nonce)
            signer_commitment = self._eth_msg_query(
                request, expected_response="antiklepto_signer_commitment"
            ).antiklepto_signer_commitment.commitment

            request = eth.ETHRequest()
            request.antiklepto_signature.CopyFrom(
                antiklepto.AntiKleptoSignatureRequest(host_nonce=host_nonce)
            )

            signature = self._eth_msg_query(request, expected_response="sign").sign.signature
            antiklepto_verify(host_nonce, signer_commitment, signature[:64])

            if self.debug:
                print(f"Antiklepto nonce verification PASSED")

            return signature

        return self._eth_msg_query(request, expected_response="sign").sign.signature

    def eth_sign_msg(
        self, msg: bytes, keypath: Sequence[int], coin: eth.ETHCoin = eth.ETH
    ) -> bytes:
        """
        Signs message, the msg will be prefixed with "\x19Ethereum message\n" + len(msg) in the
        hardware. 27 is added to the recID to denote an uncompressed pubkey.
        """

        def format_as_uncompressed(sig: bytes) -> bytes:
            # 27 is the magic constant to add to the recoverable ID to denote an uncompressed
            # pubkey.
            s = list(sig)
            s[64] += 27
            return bytes(s)

        request = eth.ETHRequest()
        # pylint: disable=no-member
        request.sign_msg.CopyFrom(eth.ETHSignMessageRequest(coin=coin, keypath=keypath, msg=msg))

        supports_antiklepto = self.version >= semver.VersionInfo(9, 5, 0)
        if supports_antiklepto:
            host_nonce = os.urandom(32)

            request.sign_msg.host_nonce_commitment.commitment = antiklepto_host_commit(host_nonce)
            signer_commitment = self._eth_msg_query(
                request, expected_response="antiklepto_signer_commitment"
            ).antiklepto_signer_commitment.commitment

            request = eth.ETHRequest()
            request.antiklepto_signature.CopyFrom(
                antiklepto.AntiKleptoSignatureRequest(host_nonce=host_nonce)
            )

            signature = self._eth_msg_query(request, expected_response="sign").sign.signature
            antiklepto_verify(host_nonce, signer_commitment, signature[:64])

            if self.debug:
                print(f"Antiklepto nonce verification PASSED")

            return format_as_uncompressed(signature)

        signature = self._eth_msg_query(request, expected_response="sign").sign.signature
        return format_as_uncompressed(signature)

    def reset(self) -> bool:
        """
        Factory reset the device. Returns True on success.
        """
        request = hww.Request()
        # pylint: disable=no-member
        request.reset.CopyFrom(bitbox02_system.ResetRequest())
        try:
            self._msg_query(request)
        except OSError:
            # In case of reboot we can't read the response.
            return True
        except Bitbox02Exception as err:
            if err.code == ERR_GENERIC:
                return False
            raise
        return True

    def restore_from_mnemonic(self) -> None:
        """
        Restore from mnemonic. Raises a Bitbox02Exception on failure.
        """
        request = hww.Request()
        # pylint: disable=no-member
        request.restore_from_mnemonic.CopyFrom(
            mnemonic.RestoreFromMnemonicRequest(
                timestamp=int(time.time()), timezone_offset=time.localtime().tm_gmtoff
            )
        )
        self._msg_query(request)
