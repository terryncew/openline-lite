"""OpenLine Lite public API."""

from .crypto import generate_private_key_hex, public_key_hex
from .chain import ChainResult, verify_native_chain
from .gate import DecisionResult, ReceiptGate
from .gateway import EvidenceGateway, IntakeResult, NativeOLPAdapter
from .mapped_adapter import AdapterProfile, MappedEd25519JSONAdapter
from .handoff import (
    HandoffFact,
    HandoffItem,
    HandoffProjection,
    build_handoff_projection,
)
from .policy import ClaimRule, Policy
from .wire import issue_source_receipt, verify_decision_receipt

__all__ = [
    "DecisionResult",
    "AdapterProfile",
    "ChainResult",
    "ClaimRule",
    "EvidenceGateway",
    "HandoffProjection",
    "HandoffFact",
    "HandoffItem",
    "IntakeResult",
    "NativeOLPAdapter",
    "MappedEd25519JSONAdapter",
    "Policy",
    "ReceiptGate",
    "generate_private_key_hex",
    "issue_source_receipt",
    "public_key_hex",
    "verify_decision_receipt",
    "verify_native_chain",
    "build_handoff_projection",
]

__version__ = "0.3.1"
