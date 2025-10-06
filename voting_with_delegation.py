from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional, Set, Tuple

from eth_account import Account
from eth_account.messages import encode_structured_data
from eth_utils import to_checksum_address


@dataclass(frozen=True)
class DelegationMessage:
    delegator: str
    delegatee: str
    nonce: int
    deadline: int


class EIP712DelegationVerifier:
    def __init__(self, *, chain_id: int, verifying_contract: str):
        self.chain_id = chain_id
        self.verifying_contract = to_checksum_address(verifying_contract)

    def build_typed_data(self, message: DelegationMessage) -> Dict:
        return {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "Delegation": [
                    {"name": "delegator", "type": "address"},
                    {"name": "delegatee", "type": "address"},
                    {"name": "nonce", "type": "uint256"},
                    {"name": "deadline", "type": "uint256"},
                ],
            },
            "primaryType": "Delegation",
            "domain": {
                "name": "XterioVoting",
                "version": "1",
                "chainId": self.chain_id,
                "verifyingContract": self.verifying_contract,
            },
            "message": {
                "delegator": to_checksum_address(message.delegator),
                "delegatee": to_checksum_address(message.delegatee),
                "nonce": int(message.nonce),
                "deadline": int(message.deadline),
            },
        }

    def recover_delegator(self, message: DelegationMessage, signature: str) -> str:
        typed = self.build_typed_data(message)
        encoded = encode_structured_data(primitive=typed)
        recovered = Account.recover_message(encoded, signature=signature)
        return to_checksum_address(recovered)


class VotingWithDelegation:
    def __init__(self, *, chain_id: int, verifying_contract: str):
        self.verifier = EIP712DelegationVerifier(
            chain_id=chain_id, verifying_contract=verifying_contract
        )
        self.voter_weight: Dict[str, int] = {}
        self.delegate_of: Dict[str, str] = {}
        self.nonce_of: Dict[str, int] = {}
        self.proposals: Dict[str, Dict] = {}

    # ---------------------- Voters & Weights ----------------------
    def add_voter(self, address: str, weight: int) -> None:
        addr = to_checksum_address(address)
        if weight < 0:
            raise ValueError("weight must be non-negative")
        self.voter_weight[addr] = weight
        if addr not in self.nonce_of:
            self.nonce_of[addr] = 0

    def get_direct_weight(self, address: str) -> int:
        return self.voter_weight.get(to_checksum_address(address), 0)

    # ---------------------- Delegation (EIP-712) ----------------------
    def get_nonce(self, delegator: str) -> int:
        return self.nonce_of.get(to_checksum_address(delegator), 0)

    def build_delegation_message(
        self, *, delegator: str, delegatee: str, deadline: int
    ) -> DelegationMessage:
        delegator_cs = to_checksum_address(delegator)
        delegatee_cs = to_checksum_address(delegatee)
        nonce = self.get_nonce(delegator_cs)
        return DelegationMessage(
            delegator=delegator_cs,
            delegatee=delegatee_cs,
            nonce=nonce,
            deadline=deadline,
        )

    def apply_delegation_signature(
        self,
        *,
        signature: str,
        delegator: str,
        delegatee: str,
        nonce: int,
        deadline: int,
    ) -> None:
        now = int(time.time())
        if now > int(deadline):
            raise ValueError("delegation signature expired")

        msg = DelegationMessage(
            delegator=to_checksum_address(delegator),
            delegatee=to_checksum_address(delegatee),
            nonce=int(nonce),
            deadline=int(deadline),
        )

        expected_nonce = self.get_nonce(msg.delegator)
        if msg.nonce != expected_nonce:
            raise ValueError("invalid nonce for delegator")

        recovered = self.verifier.recover_delegator(msg, signature)
        if recovered != msg.delegator:
            raise ValueError("invalid signature: signer is not delegator")

        self._set_delegate(msg.delegator, msg.delegatee)
        self.nonce_of[msg.delegator] = expected_nonce + 1

    def _set_delegate(self, delegator: str, delegatee: str) -> None:
        delegator_cs = to_checksum_address(delegator)
        delegatee_cs = to_checksum_address(delegatee)
        if delegator_cs == delegatee_cs:
            # Self-delegation cancels any existing delegation chain
            self.delegate_of.pop(delegator_cs, None)
            return
        # Detect cycles
        if self._would_create_cycle(delegator_cs, delegatee_cs):
            raise ValueError("delegation would create a cycle")
        self.delegate_of[delegator_cs] = delegatee_cs

    def _would_create_cycle(self, start: str, next_hop: str) -> bool:
        seen: Set[str] = set()
        current = to_checksum_address(next_hop)
        start_cs = to_checksum_address(start)
        while current in self.delegate_of:
            if current in seen:
                return True
            seen.add(current)
            current = self.delegate_of[current]
            if current == start_cs:
                return True
        return False

    def _resolve_final_delegate(self, addr: str) -> str:
        addr_cs = to_checksum_address(addr)
        current = addr_cs
        seen: Set[str] = set()
        while current in self.delegate_of:
            if current in seen:
                break
            seen.add(current)
            current = self.delegate_of[current]
        return current

    def get_effective_voting_power_map(self) -> Dict[str, int]:
        power: Dict[str, int] = {}
        for voter, weight in self.voter_weight.items():
            if weight == 0:
                continue
            final_holder = self._resolve_final_delegate(voter)
            power[final_holder] = power.get(final_holder, 0) + weight
        return power

    def get_effective_voting_power(self, address: str) -> int:
        final_holder = self._resolve_final_delegate(address)
        return self.get_effective_voting_power_map().get(final_holder, 0)

    # ---------------------- Proposals & Voting ----------------------
    def create_proposal(
        self, *, proposal_id: str, title: str, description: str, closes_at: int
    ) -> None:
        if proposal_id in self.proposals:
            raise ValueError("proposal already exists")
        if int(closes_at) <= int(time.time()):
            raise ValueError("closes_at must be in the future")
        self.proposals[proposal_id] = {
            "title": title,
            "description": description,
            "closes_at": int(closes_at),
            "tallies": {"yes": 0, "no": 0, "abstain": 0},
            "voted": set(),
        }

    def vote(self, *, proposal_id: str, voter: str, choice: str) -> Tuple[int, Dict[str, int]]:
        if proposal_id not in self.proposals:
            raise ValueError("unknown proposal")
        if choice not in ("yes", "no", "abstain"):
            raise ValueError("invalid choice")
        prop = self.proposals[proposal_id]
        if int(time.time()) > int(prop["closes_at"]):
            raise ValueError("voting closed")

        voter_cs = to_checksum_address(voter)
        final_holder = self._resolve_final_delegate(voter_cs)

        if voter_cs != final_holder:
            raise ValueError("delegated voters cannot cast a direct vote")

        if final_holder in prop["voted"]:
            raise ValueError("already voted for this proposal")

        power_map = self.get_effective_voting_power_map()
        weight = power_map.get(final_holder, 0)
        if weight <= 0:
            raise ValueError("no voting power")

        prop["tallies"][choice] += int(weight)
        prop["voted"].add(final_holder)
        return weight, dict(prop["tallies"])  # return snapshot

    def get_results(self, proposal_id: str) -> Dict[str, int]:
        if proposal_id not in self.proposals:
            raise ValueError("unknown proposal")
        return dict(self.proposals[proposal_id]["tallies"])

    def is_open(self, proposal_id: str) -> bool:
        if proposal_id not in self.proposals:
            raise ValueError("unknown proposal")
        return int(time.time()) <= int(self.proposals[proposal_id]["closes_at"])


__all__ = [
    "DelegationMessage",
    "EIP712DelegationVerifier",
    "VotingWithDelegation",
]


