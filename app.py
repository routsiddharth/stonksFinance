import io
import os
import requests
import time
import matplotlib
import sqlite3

# Used solution from https://stackoverflow.com/a/2267304/14685194 to prevent DEBUG messages
# logging.basicConfig(level=logging.ERROR)

from flask import Flask, flash, redirect, render_template, request, session, Response
from flask_session.__init__ import Session
from tempfile import mkdtemp
from werkzeug.exceptions import default_exceptions, HTTPException, InternalServerError
from werkzeug.security import check_password_hash, generate_password_hash
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from matplotlib.figure import Figure
from helpers import apology, login_required, lookup, usd

matplotlib.use('agg')

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

@app.after_request
def after_request(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Expires"] = 0
    response.headers["Pragma"] = "no-cache"
    return response


app.jinja_env.filters["usd"] = usd
app.config["SESSION_FILE_DIR"] = mkdtemp()
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(BASE_DIR, "finance.db")

con = sqlite3.connect(db_path, check_same_thread=False)
con.row_factory = sqlite3.Row

cur = con.cursor()

API_KEY = "pk_320446f105ef4f87839c51ec067b323d"

@app.route("/login", methods=["GET", "POST"])
def login():

    # Forget any user_id
    session.clear()

    if request.method == "POST":

        username = request.form.get("username").lower()
        password = request.form.get("password")

        if not username or not password:
            return apology("Please enter a valid username and password")

        # Reformat() function changes the output format of the cursor.execute() function

        rows = reformat(cur.execute(f"SELECT * FROM users WHERE username = '{username}'"), "users")

        print(rows)

        if len(rows) != 1 or not check_password_hash(rows[0]["hash"], password):
            return apology("Invalid username and/or password")

        session["user_id"] = rows[0]["id"]

        return redirect("/")

    else:
        return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        username = request.form.get("username").lower()
        password = request.form.get("password")
        confirmation = request.form.get("confirmation")

        if not username or not password or not confirmation:
            return apology("All fields must be filled in")

        if password != confirmation:
            return apology("Password and confirmation do not match")

        existing = reformat(cur.execute(f"SELECT * FROM users WHERE username = '{username}';"), "users")

        if existing != []:
            return apology("An account with this username already exists")

        passwordhash = generate_password_hash(password)
        cur.execute(f"INSERT INTO users (username, hash) VALUES ('{username}', '{passwordhash}');")
        user_id = reformat(cur.execute(f"SELECT * FROM users WHERE username = '{username}';"), "users")[0]["id"]

        cur.execute(
            f"CREATE TABLE owned{user_id} (ticker TEXT, shares INTEGER, buy_price NUMERIC, current_price NUMERIC, pl NUMERIC)")
        return redirect("/login")

    else:
        return render_template("register.html")


@app.route("/")
@login_required
def index():

    user = session['user_id']

    username = reformat(cur.execute(f"SELECT * FROM users WHERE id = {user};"), "users")[0]["username"]
    portfolio = reformat(cur.execute(f"SELECT * FROM owned{user};"), "owned")

    totalHoldings = 0

    for stock in portfolio:

        # Update stock price
        ticker = stock['ticker']
        
        # This was a ticker I used to test the functions without constantly requesting from API
        if ticker == "AAAA":
            currentPrice = 28

        else:
            currentPrice = float(requests.get(
                f"https://cloud.iexapis.com/stable/stock/{ticker}/quote?token={API_KEY}").json()["latestPrice"])

        buyPrice = float(stock['buy_price'])
        pl = round((currentPrice - buyPrice)/buyPrice * 100, 2)

        cur.execute(f"UPDATE owned{user} SET current_price = {currentPrice}, pl = {pl} WHERE ticker = '{ticker}';")

        # Calculate total holdings size
        shares = int(stock['shares'])
        totalHoldings += shares * currentPrice

    portfolio = reformat(cur.execute(f"SELECT * FROM owned{user} ORDER BY ticker;"), "owned")
    cash = reformat(cur.execute(f"SELECT * FROM users WHERE id = {user}"), "users")[0]["cash"]

    totalHoldings += cash

    assets = cash

    for stock in portfolio:
        value = stock["shares"] * stock["current_price"]
        assets = assets + value

    cur.execute(f"UPDATE users SET assets = {assets} WHERE id = {user}")

    return render_template("index.html", username=username, portfolio=portfolio, cash=cash, totalHoldings=totalHoldings)


@app.route("/quote", methods=["GET", "POST"])
@login_required
def quote():

    global chartTicker, chartTimeframe

    if request.method == "POST":

        ticker = request.form.get("symbol")
        timeframe = request.form.get("timeframe")

        if not ticker or not timeframe:
            return apology("Please fill in both fields")

        response = requests.get(f"https://cloud.iexapis.com/stable/stock/{ticker}/quote?token={API_KEY}")

        if response.status_code != 200:
            return apology("Please enter a valid stock ticker")

        stockData = response.json()

    else:

        stockData = requests.get(f"https://cloud.iexapis.com/stable/stock/nflx/quote?token={API_KEY}").json()
        timeframe = 30

    values = {}

    values["company"] = stockData["companyName"]
    values["ticker"] = stockData["symbol"]
    values["price"] = stockData["latestPrice"]

    marketCap = stockData["marketCap"]

    if marketCap > 1000000000000:
        marketCap = f"{round(marketCap / 1000000000000, 2)} trillion USD"
    elif marketCap > 1000000000:
        marketCap = f"{round(marketCap / 1000000000, 2)} billion USD"
    elif marketCap > 1000000:
        marketCap = f"{round(marketCap / 1000000, 2)} million USD"
    elif marketCap > 1000:
        marketCap = f"{round(marketCap / 1000, 2)} thousand USD"

    values["marketCap"] = marketCap

    chartTicker = values["ticker"]
    chartTimeframe = timeframe

    return render_template("quote.html", values=values)

@app.route('/plot.png')
def plot_png():

    fig = create_figure()

    output = io.BytesIO()
    FigureCanvas(fig).print_png(output)
    return Response(output.getvalue(), mimetype='image/png')


def create_figure():

    fig = Figure()
    axis = fig.add_subplot(1, 1, 1)

    historical = requests.get(
        f"http://api.marketstack.com/v1/eod?access_key=229c65f839b3fc236d170ccf290f2a82&symbols={chartTicker}&limit={chartTimeframe}").json()["data"]

    xVals = []
    yVals = []

    count = 1

    for stat in historical:

        xVals.append(-count)
        yVals.append(float(stat["adj_close"]))

        count += 1

    axis.plot(xVals, yVals)

    fig.suptitle(f"{chartTicker} Price: {chartTimeframe} days")

    axis.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)

    return fig


@app.route("/buy", methods=["GET", "POST"])
@login_required
def buy():

    if request.method == "POST":

        user = session["user_id"]
        ticker = request.form.get("symbol").upper()
        shares = float(request.form.get("shares"))

        if int(shares % 1) != 0:
            return apology("Please input a whole number of shares")

        if not ticker or not shares:
            return apology("One or more fields missing")

        response = requests.get(f"https://cloud.iexapis.com/stable/stock/{ticker}/quote?token={API_KEY}")

        if response.status_code != 200:
            return apology("Please enter a valid stock ticker")

        response = response.json()

        sharePrice = float(response["latestPrice"])
        price = round(sharePrice*shares, 2)
        balance = reformat(cur.execute(f"SELECT * FROM users WHERE id = {user};"), "users")[0]["cash"]

        if price > balance:
            return apology("Not enough balance")

        response = reformat(cur.execute(f"SELECT * FROM owned{user} WHERE ticker = '{ticker}'"), "owned")

        if response == []:
            cur.execute(
                f"INSERT INTO owned{user} (ticker, shares, buy_price, current_price, pl) VALUES ('{ticker}', {shares}, {sharePrice}, {sharePrice}, 0);")

        else:

            previousAvg = reformat(cur.execute(f"SELECT * FROM owned{user} WHERE ticker = '{ticker}'"), "owned")[0]["buy_price"]
            previousOwned = reformat(cur.execute(f"SELECT * FROM owned{user} WHERE ticker = '{ticker}'"), "owned")[0]["shares"]

            avgOpen = round((previousAvg * previousOwned + price) / (previousOwned + shares), 2)

            cur.execute(
                f"UPDATE owned{user} SET shares = (SELECT shares FROM owned{user} WHERE ticker = '{ticker}') + {shares}, buy_price = {avgOpen} WHERE ticker = '{ticker}';")

        cur.execute(f"UPDATE users SET cash = {balance - price} WHERE id = {user}")
        cur.execute(
            f"INSERT INTO transactions (id, type, ticker, shares, time, share_price, transaction_price) VALUES ({user}, 'buy', '{ticker}', {shares}, '{str(time.ctime())}', {sharePrice}, {price});")

        return redirect("/")

    else:
        return redirect("/")


@app.route("/sell", methods=["GET", "POST"])
@login_required
def sell():

    if request.method == "POST":

        user = session["user_id"]
        ticker = request.form.get("symbol").upper()
        shares = float(request.form.get("shares"))

        if int(shares % 1) != 0:
            return apology("Please enter a whole number of shares")

        if not ticker or not shares:
            return apology("Please enter a valid stock ticker and a positive amount of shares")

        response = reformat(cur.execute(f"SELECT * FROM owned{user} WHERE ticker = '{ticker}' AND shares >= {shares}"), "owned")

        if response == []:
            return apology(f"You do not have enough shares of {ticker} to sell")

        ownedShares = response[0]["shares"]

        response = requests.get(f"https://cloud.iexapis.com/stable/stock/{ticker}/quote?token={API_KEY}").json()

        sharePrice = response["latestPrice"]
        value = round(shares * sharePrice, 2)

        if ownedShares == shares:
            cur.execute(f"DELETE FROM owned{user} WHERE ticker = '{ticker}';")
        else:
            cur.execute(
                f"UPDATE owned{user} SET shares = {ownedShares - shares}, current_price = {sharePrice} WHERE ticker == '{ticker}';")

        cash = reformat(cur.execute(f"SELECT * FROM users WHERE id = {user};"), "users")[0]["cash"]

        cur.execute(f"UPDATE users SET cash = {round(cash + value, 2)} WHERE id = {user};")
        cur.execute(
            f"INSERT INTO transactions (id, type, ticker, shares, time, share_price, transaction_price) VALUES ({user}, 'sell', '{ticker}', {shares}, '{str(time.ctime())}', {sharePrice}, {value});")

        return redirect("/")

    else:

        return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/history")
@login_required
def history():

    user = session["user_id"]
    userHistory = reformat(cur.execute(f"SELECT * FROM transactions WHERE id = {user}"), "transactions")[::-1]

    return render_template("history.html", transactions=userHistory)


@app.route("/users", methods=["GET", "POST"])
@login_required
def users():

    if request.method == "POST":
        username = request.form.get("username").lower()
        response = reformat(cur.execute(f"SELECT * FROM users WHERE username = '{username}';"), "users")

        if len(response) != 1:
            return apology("User doesn't exist")

        user_id = response[0]["id"]

        owned = reformat(cur.execute(f"SELECT * FROM owned{user_id};"), "owned")

        for stock in owned:

            ticker = stock['ticker']

            if ticker == "AAAA":
                currentPrice = 28

            else:
                currentPrice = float(requests.get(
                    f"https://cloud.iexapis.com/stable/stock/{ticker}/quote?token={API_KEY}").json()["latestPrice"])

            buyPrice = float(stock['buy_price'])
            pl = round((currentPrice - buyPrice)/buyPrice * 100, 2)

            cur.execute(f"UPDATE owned{user_id} SET current_price = {currentPrice}, pl = {pl} WHERE ticker = '{ticker}';")

        owned = reformat(cur.execute(f"SELECT * FROM owned{user_id};"), "owned")

        # When passed into the template, variable is called transactions but it isn't transactions!
        # It is current holdings. There is an issue with any other variable name, trying to fix it.
        return render_template("userinfo.html", user=response[0], transactions=owned)

    else:
        return render_template("searchusers.html")


def reformat(data, type):
    if type == "users":
        columns = ["id", "username", "hash", "cash", "assets"]
    elif type == "transactions":
        columns = ["id", "type", "ticker", "shares", "time", "share_price", "transaction_price"]
    else:
        columns = ["ticker", "shares", "buy_price", "current_price", "pl"]

    returned = []

    for line in data:

        d = {}

        for i in range(len(line)):
            try:
                d[columns[i]] = round(line[i], 2)
            except:
                d[columns[i]] = line[i]

        returned.append(d)

    return returned


def errorhandler(e):
    if not isinstance(e, HTTPException):
        e = InternalServerError()
    return apology(e.name, e.code)


# Listen for errors
for code in default_exceptions:
    app.errorhandler(code)(errorhandler)
