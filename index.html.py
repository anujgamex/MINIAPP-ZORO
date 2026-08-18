<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>A.G.W. Withdrawal Portal</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        :root {
            --bg-color: var(--tg-theme-bg-color, #121212);
            --text-color: var(--tg-theme-text-color, #ffffff);
            --hint-color: var(--tg-theme-hint-color, #888888);
            --button-color: var(--tg-theme-button-color, #00ffcc);
            --button-text-color: var(--tg-theme-button-text-color, #000000);
            --secondary-bg: var(--tg-theme-secondary-bg-color, #1e1e1e);
        }
        body {
            font-family: 'Arial', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            padding: 20px;
            margin: 0;
            box-sizing: border-box;
        }
        .header {
            text-align: center;
            margin-bottom: 25px;
        }
        .header h1 {
            margin: 0;
            font-size: 24px;
            text-transform: uppercase;
            letter-spacing: 2px;
            color: var(--button-color);
        }
        .header p {
            margin: 5px 0 0 0;
            font-size: 14px;
            color: var(--hint-color);
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 8px;
            font-size: 14px;
            font-weight: bold;
        }
        input, select {
            width: 100%;
            padding: 14px;
            border-radius: 8px;
            border: 1px solid #333;
            background-color: var(--secondary-bg);
            color: var(--text-color);
            font-size: 16px;
            box-sizing: border-box;
            outline: none;
        }
        input:focus, select:focus {
            border-color: var(--button-color);
        }
        button {
            width: 100%;
            padding: 16px;
            border-radius: 8px;
            background-color: var(--button-color);
            color: var(--button-text-color);
            font-size: 16px;
            font-weight: bold;
            border: none;
            cursor: pointer;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-top: 10px;
        }
    </style>
</head>
<body>

    <div class="header">
        <h1>A.G.W. Portal</h1>
        <p>Fast & Secure Withdrawals</p>
    </div>

    <form id="withdrawForm">
        <div class="form-group">
            <label for="amount">Withdrawal Amount (Min ₹20):</label>
            <input type="number" id="amount" placeholder="Enter amount..." min="20" required>
        </div>

        <div class="form-group">
            <label for="speed">Transfer Speed:</label>
            <select id="speed" required>
                <option value="std">Standard (24-48 hrs, Free)</option>
                <option value="urg">Urgent (1-2 hrs, ₹15 Fee)</option>
            </select>

