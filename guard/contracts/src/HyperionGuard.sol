// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {Ownable2Step, Ownable} from "@openzeppelin/contracts/access/Ownable2Step.sol";

/// @title HyperionGuard
/// @notice The on-chain control plane for autonomous trading agents.
///
/// A trading agent's human owner registers the agent here with its risk
/// policy. The Hyperion Guard service checks every order the agent proposes
/// against that policy and returns a signed verdict. Anything that acts on an
/// agent's behalf (a venue, a vault, `GuardedExecutor`) can ask this contract
/// whether a verdict is valid right now.
///
/// What lives on-chain, and why:
///   * The policy, so the agent's limits are public and only its owner can
///     change them. The agent's own key cannot loosen them.
///   * The kill switch. Arc finalises in under a second, so an owner (or a
///     guardian such as a monitoring bot) can stop an agent everywhere at
///     once, and a compromised agent cannot undo it. Killing is easy (owner
///     or guardian); reviving is not (owner only).
///   * Verdict validity: signer, expiry, policy version and kill state are
///     checked together, so a verdict issued before a kill or a policy
///     change is dead the moment either happens.
///   * An audit trail: the Guard anchors Merkle roots of every verdict it
///     issued, in contiguous sequence ranges, so its record can be checked
///     later and gaps are impossible to hide.
contract HyperionGuard is EIP712, Ownable2Step {
    /// Limits the Guard service enforces. Notional amounts are in USDC's
    /// 6-decimal units (1 USDC = 1_000_000).
    struct Policy {
        uint64 maxOrderNotional; // largest single order
        uint64 maxDailyNotional; // total over a UTC day
        uint16 collarBps; // max distance from the reference price, in basis points
        uint16 maxOrdersPerMinute; // throttle
    }

    struct Agent {
        address owner; // may change policy, kill, revive, set the guardian
        address guardian; // may only kill; zero if none
        bool killed;
        uint32 policyVersion; // bumped on every policy change and revive
        Policy policy;
    }

    /// A decision by the Guard service about one proposed order.
    struct Verdict {
        address agent;
        bytes32 orderHash; // hash of the order as the agent proposed it
        bool approved;
        uint16 reason; // 0 when approved; otherwise a rejection code (see docs)
        uint32 policyVersion; // the version the order was checked against
        uint64 seq; // the Guard's sequence number, for the audit trail
        uint64 expiresAt; // unix seconds; verdicts are short-lived
    }

    enum Status {
        Valid,
        UnknownAgent,
        Killed,
        StalePolicy,
        Expired,
        NotApproved,
        BadSignature
    }

    bytes32 public constant VERDICT_TYPEHASH = keccak256(
        "Verdict(address agent,bytes32 orderHash,bool approved,uint16 reason,uint32 policyVersion,uint64 seq,uint64 expiresAt)"
    );

    /// Longest life a verdict may claim. Short, so an approval can't be
    /// stockpiled and replayed long after the market or the policy moved.
    uint64 public constant MAX_VERDICT_TTL = 5 minutes;

    address public guardSigner;
    uint64 public lastAnchoredSeq;
    mapping(address agent => Agent) private _agents;

    event GuardSignerChanged(address indexed previous, address indexed current);
    event AgentRegistered(address indexed agent, address indexed owner, Policy policy);
    event PolicyChanged(address indexed agent, uint32 version, Policy policy);
    event GuardianChanged(address indexed agent, address indexed guardian);
    event AgentOwnershipTransferred(address indexed agent, address indexed previousOwner, address indexed newOwner);
    event AgentKilled(address indexed agent, address indexed by);
    event AgentRevived(address indexed agent, uint32 version);
    event VerdictsAnchored(uint64 indexed firstSeq, uint64 indexed lastSeq, bytes32 root);

    error ZeroAddress();
    error AgentExists();
    error NotAgentOwner();
    error NotOwnerOrGuardian();
    error UnknownAgentError();
    error NotGuardSigner();
    error BadSequence(uint64 expectedFirst);
    error InvalidPolicy();

    constructor(address initialAdmin, address initialSigner)
        EIP712("HyperionGuard", "1")
        Ownable(initialAdmin)
    {
        if (initialSigner == address(0)) revert ZeroAddress();
        guardSigner = initialSigner;
        emit GuardSignerChanged(address(0), initialSigner);
    }

    // ── admin ────────────────────────────────────────────────────────────────

    /// Rotating the signer invalidates every outstanding verdict at once.
    function setGuardSigner(address signer) external onlyOwner {
        if (signer == address(0)) revert ZeroAddress();
        emit GuardSignerChanged(guardSigner, signer);
        guardSigner = signer;
    }

    // ── agents ───────────────────────────────────────────────────────────────

    modifier onlyAgentOwner(address agent) {
        if (_agents[agent].owner != msg.sender) revert NotAgentOwner();
        _;
    }

    /// Register `agent` with the caller as its owner. The agent key and the
    /// owner key should differ: the point is that the agent can't govern itself.
    function registerAgent(address agent, Policy calldata policy, address guardian) external {
        if (agent == address(0)) revert ZeroAddress();
        if (_agents[agent].owner != address(0)) revert AgentExists();
        _validate(policy);
        _agents[agent] = Agent({owner: msg.sender, guardian: guardian, killed: false, policyVersion: 1, policy: policy});
        emit AgentRegistered(agent, msg.sender, policy);
        emit PolicyChanged(agent, 1, policy);
        if (guardian != address(0)) emit GuardianChanged(agent, guardian);
    }

    function setPolicy(address agent, Policy calldata policy) external onlyAgentOwner(agent) {
        _validate(policy);
        Agent storage a = _agents[agent];
        a.policy = policy;
        unchecked {
            a.policyVersion += 1;
        }
        emit PolicyChanged(agent, a.policyVersion, policy);
    }

    function setGuardian(address agent, address guardian) external onlyAgentOwner(agent) {
        _agents[agent].guardian = guardian;
        emit GuardianChanged(agent, guardian);
    }

    function transferAgentOwnership(address agent, address newOwner) external onlyAgentOwner(agent) {
        if (newOwner == address(0)) revert ZeroAddress();
        emit AgentOwnershipTransferred(agent, msg.sender, newOwner);
        _agents[agent].owner = newOwner;
    }

    /// Stop the agent. Every verdict, past and future, fails until revived.
    function kill(address agent) external {
        Agent storage a = _agents[agent];
        if (a.owner == address(0)) revert UnknownAgentError();
        if (msg.sender != a.owner && msg.sender != a.guardian) revert NotOwnerOrGuardian();
        a.killed = true;
        emit AgentKilled(agent, msg.sender);
    }

    /// Only the owner can revive, and reviving bumps the policy version, so
    /// verdicts issued before the kill never come back to life.
    function revive(address agent) external onlyAgentOwner(agent) {
        Agent storage a = _agents[agent];
        a.killed = false;
        unchecked {
            a.policyVersion += 1;
        }
        emit AgentRevived(agent, a.policyVersion);
    }

    function agentOf(address agent) external view returns (Agent memory) {
        return _agents[agent];
    }

    // ── verdicts ─────────────────────────────────────────────────────────────

    function hashVerdict(Verdict calldata v) public view returns (bytes32) {
        return _hashTypedDataV4(
            keccak256(
                abi.encode(
                    VERDICT_TYPEHASH, v.agent, v.orderHash, v.approved, v.reason, v.policyVersion, v.seq, v.expiresAt
                )
            )
        );
    }

    /// Whether `v` is a live approval: signed by the current Guard signer,
    /// approved, unexpired, for a registered agent that isn't killed, and
    /// checked against the agent's current policy.
    function check(Verdict calldata v, bytes calldata signature) external view returns (Status) {
        Agent storage a = _agents[v.agent];
        if (a.owner == address(0)) return Status.UnknownAgent;
        if (a.killed) return Status.Killed;
        if (v.policyVersion != a.policyVersion) return Status.StalePolicy;
        if (block.timestamp > v.expiresAt || v.expiresAt > block.timestamp + MAX_VERDICT_TTL) return Status.Expired;
        if (!v.approved) return Status.NotApproved;
        (address signer, ECDSA.RecoverError err,) = ECDSA.tryRecover(hashVerdict(v), signature);
        if (err != ECDSA.RecoverError.NoError || signer != guardSigner) return Status.BadSignature;
        return Status.Valid;
    }

    // ── audit trail ──────────────────────────────────────────────────────────

    /// Anchor the Merkle root of verdicts `firstSeq`..`lastSeq`. Ranges must
    /// be contiguous, so a gap in the Guard's record can't be hidden.
    function anchor(uint64 firstSeq, uint64 lastSeq, bytes32 root) external {
        if (msg.sender != guardSigner) revert NotGuardSigner();
        if (firstSeq != lastAnchoredSeq + 1 || lastSeq < firstSeq) revert BadSequence(lastAnchoredSeq + 1);
        lastAnchoredSeq = lastSeq;
        emit VerdictsAnchored(firstSeq, lastSeq, root);
    }

    function _validate(Policy calldata p) private pure {
        if (
            p.maxOrderNotional == 0 || p.maxDailyNotional < p.maxOrderNotional || p.collarBps == 0
                || p.collarBps > 5_000 || p.maxOrdersPerMinute == 0
        ) revert InvalidPolicy();
    }
}
