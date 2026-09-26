// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {HyperionGuard} from "./HyperionGuard.sol";

/// @title GuardedExecutor
/// @notice A wallet an autonomous trading agent can trade from, but only
/// with the Guard's approval for that exact call.
///
/// The owner funds it and lists the contracts it may call (a DEX router, a
/// token to approve). The agent calls `execute` with the call, its declared
/// USDC notional, and a Guard verdict. The verdict must cover the hash of
/// exactly that call at the current nonce, and `HyperionGuard.check` must
/// say it's live. So the agent can't reuse an approval, change the call
/// after approval, call anything unlisted, or act at all once killed.
///
/// What this doesn't do: read the notional out of arbitrary calldata. The
/// agent declares it, and the Guard checks the declared figure against the
/// call it's shown. The target allow-list bounds what a lie could reach.
contract GuardedExecutor is ReentrancyGuard {
    using SafeERC20 for IERC20;

    HyperionGuard public immutable guard;
    address public immutable owner;
    address public immutable agent;

    uint256 public nonce;
    mapping(address target => bool) public allowedTarget;

    event TargetAllowed(address indexed target, bool allowed);
    event Executed(uint256 indexed nonce, address indexed target, uint256 notional, uint64 verdictSeq);
    event Withdrawn(address indexed token, address indexed to, uint256 amount);

    error NotOwner();
    error NotAgent();
    error TargetNotAllowed(address target);
    error WrongOrder();
    error VerdictRejected(HyperionGuard.Status status);
    error CallFailed(bytes returnData);

    constructor(HyperionGuard guard_, address owner_, address agent_) {
        guard = guard_;
        owner = owner_;
        agent = agent_;
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    function setAllowedTarget(address target, bool allowed) external onlyOwner {
        allowedTarget[target] = allowed;
        emit TargetAllowed(target, allowed);
    }

    /// The owner can always take funds out, whatever the agent's state.
    function withdraw(IERC20 token, address to, uint256 amount) external onlyOwner {
        token.safeTransfer(to, amount);
        emit Withdrawn(address(token), to, amount);
    }

    /// The hash the Guard signs for a call. Binds the chain, this wallet,
    /// the target, the calldata, the declared notional and the nonce.
    function orderHash(address target, bytes calldata data, uint256 notional, uint256 nonce_)
        public
        view
        returns (bytes32)
    {
        return keccak256(abi.encode(block.chainid, address(this), target, keccak256(data), notional, nonce_));
    }

    function execute(
        address target,
        bytes calldata data,
        uint256 notional,
        HyperionGuard.Verdict calldata verdict,
        bytes calldata signature
    ) external nonReentrant returns (bytes memory) {
        if (msg.sender != agent) revert NotAgent();
        if (!allowedTarget[target]) revert TargetNotAllowed(target);
        if (verdict.agent != agent || verdict.orderHash != orderHash(target, data, notional, nonce)) {
            revert WrongOrder();
        }
        HyperionGuard.Status status = guard.check(verdict, signature);
        if (status != HyperionGuard.Status.Valid) revert VerdictRejected(status);

        uint256 used = nonce;
        unchecked {
            nonce = used + 1; // before the call: a reentrant replay sees the next nonce
        }
        (bool ok, bytes memory ret) = target.call(data);
        if (!ok) revert CallFailed(ret);
        emit Executed(used, target, notional, verdict.seq);
        return ret;
    }
}
