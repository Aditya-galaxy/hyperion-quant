// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

/// @title DemoVenue
/// @notice A stand-in exchange for the Hyperion Guard demo. It moves no
/// money: it records each order it's sent, so the demo can show on-chain
/// which orders got through a GuardedExecutor and which were stopped.
contract DemoVenue {
    struct Fill {
        address trader;
        bytes32 symbol;
        bool buy;
        uint256 qty; // 1e8 = 1 unit
        uint256 price; // 1e8 = 1 USD
    }

    Fill[] public fills;

    event Filled(uint256 indexed id, address indexed trader, bytes32 symbol, bool buy, uint256 qty, uint256 price);

    function placeOrder(bytes32 symbol, bool buy, uint256 qty, uint256 price) external returns (uint256 id) {
        id = fills.length;
        fills.push(Fill(msg.sender, symbol, buy, qty, price));
        emit Filled(id, msg.sender, symbol, buy, qty, price);
    }

    function fillCount() external view returns (uint256) {
        return fills.length;
    }
}
