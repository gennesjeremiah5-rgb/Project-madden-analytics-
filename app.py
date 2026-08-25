# =========================================================
# PROJECT MADDEN GAME ANALYST
# NON-REPETITIVE REACTION ENGINE
# =========================================================

ANALYST_HISTORY_FILE = "analyst_history.json"


# =========================================================
# ANALYST HISTORY
# =========================================================

def load_analyst_history():

    history = load_json_file(
        ANALYST_HISTORY_FILE
    )

    if not isinstance(
        history,
        dict
    ):
        history = {}

    return history


def save_analyst_history(
    history
):

    save_json_file(
        ANALYST_HISTORY_FILE,
        history
    )


def unique_analyst_choice(
    category,
    options,
    key
):

    if not options:
        return ""

    history = load_analyst_history()

    recent = history.get(
        category,
        []
    )

    available = [
        option
        for option in options
        if option not in recent
    ]

    if not available:

        available = options[:]

        recent = []

    digest = hashlib.sha256(
        (
            str(key)
            +
            category
        ).encode(
            "utf-8"
        )
    ).hexdigest()

    index = (
        int(
            digest[:8],
            16
        )
        %
        len(
            available
        )
    )

    selected = available[
        index
    ]

    recent.append(
        selected
    )

    # Keep only the latest lines
    recent = recent[-8:]

    history[
        category
    ] = recent

    save_analyst_history(
        history
    )

    return selected


# =========================================================
# ANALYST PERSONALITY
# =========================================================

ANALYST_OPENINGS = [

    "I need everybody to understand what we just watched.",

    "There is no way I'm brushing this result aside.",

    "We have to talk about what happened in this game.",

    "This is exactly why you cannot judge a team on reputation alone.",

    "Somebody needs to explain this performance to me.",

    "I watched this game from beginning to end, and I have plenty to say.",

    "This result deserves attention because it told us a lot.",

    "Forget the excuses. Let's talk about what actually happened on the field.",

    "I've seen enough to have a very strong opinion about this one.",

    "This is one of those games where the final score tells only part of the story.",

    "I don't want to hear about expectations right now. I want to talk about execution.",

    "This game gave us plenty of evidence about where these teams stand.",

    "Ladies and gentlemen, this is exactly the kind of performance that gets people talking.",

    "There are wins, there are losses, and then there are games that send a message.",

    "The film is going to be uncomfortable for somebody after this one."

]


ANALYST_POSITIVE_CLOSERS = [

    "If they keep playing like this, the rest of the league needs to pay attention.",

    "That is the type of performance that earns respect.",

    "You don't have to like them, but you better take them seriously.",

    "That looked like a team that knew exactly what it wanted to accomplish.",

    "They earned every bit of praise coming their way after this one.",

    "This is how you make people stop doubting you.",

    "Put this performance on the résumé because it mattered.",

    "The standard just got raised after a game like that.",

    "They did their talking on the field, and that's what matters.",

    "That was professional, composed and impressive."

]


ANALYST_NEGATIVE_CLOSERS = [

    "They better get this fixed before another opponent exposes the same problem.",

    "That cannot become a habit if this team expects to contend.",

    "The coaching staff has plenty to clean up after that performance.",

    "You cannot keep putting yourself in those situations and expect different results.",

    "This team needs answers, because what we saw was not good enough.",

    "The film room is going to be very uncomfortable after this one.",

    "If they don't correct this quickly, the criticism is only going to get louder.",

    "This is the kind of performance that forces everybody in the building to look in the mirror.",

    "No excuses. Get back to work and fix it.",

    "A serious team cannot be satisfied with what it put on the field."

]


# =========================================================
# GAME REACTION BANKS
# =========================================================

BLOWOUT_REACTIONS = [

    "{winner} didn't just beat {loser}. They controlled this game and made the difference between these teams look enormous.",

    "This was domination. {winner} dictated the game while {loser} spent most of the night searching for answers.",

    "There was nothing accidental about this result. {winner} imposed its will and never gave {loser} a chance to settle in.",

    "{loser} got overwhelmed. The score reflects exactly how one-sided this game became.",

    "When a game gets this lopsided, you have to give {winner} credit and you also have to ask some serious questions about {loser}.",

    "{winner} took control early and never gave it back. That is what a complete performance looks like.",

    "This looked less like a competitive matchup and more like {winner} making a statement at {loser}'s expense.",

    "The difference in execution was obvious. {winner} was sharper, more physical and much more composed."

]


UPSET_REACTIONS = [

    "{winner} came into this game as the lower-rated team and clearly did not care what the ratings said.",

    "Throw the overall ratings out the window. {winner} walked onto the field and beat the team that was supposed to have the advantage.",

    "This is why games aren't decided on the roster screen. {winner} earned this upset on the field.",

    "{loser} had the ratings advantage and still couldn't finish the job. That is going to sting.",

    "Nobody should dismiss {winner} after this. They just took down a team that had the advantage on paper.",

    "The ratings told us one thing. The scoreboard told us something completely different.",

    "{winner} just reminded everybody that execution matters more than numbers next to a team name.",

    "If {loser} thought the ratings were going to win this game for them, {winner} gave them a harsh reality check."

]


CLOSE_GAME_REACTIONS = [

    "This game came down to execution in the biggest moments, and {winner} found a way to finish.",

    "There was almost nothing separating these teams, but {winner} made enough plays when the pressure was highest.",

    "{loser} had opportunities to steal this game, but {winner} survived every push.",

    "This was a heavyweight fight all the way to the end, and {winner} landed the final meaningful punch.",

    "Nobody ran away with this game. {winner} simply handled the decisive moments better.",

    "When the margin is this small, every possession matters. {winner} understood that just a little better.",

    "{loser} is going to replay several moments from this game because it was absolutely there for the taking.",

    "This was tense, competitive football, and {winner} deserves credit for staying composed."

]


NORMAL_WIN_REACTIONS = [

    "{winner} was the better team today and the scoreboard reflected it.",

    "{winner} handled its business. It wasn't perfect, but it was convincing enough.",

    "There were moments where {loser} threatened to make this interesting, but {winner} always seemed to have an answer.",

    "{winner} played with better control and ultimately deserved this result.",

    "You don't need every victory to be dramatic. {winner} simply went out and earned one.",

    "{winner} did enough in all the important areas to leave with the win.",

    "The difference wasn't overwhelming, but {winner} consistently made the better plays.",

    "{loser} competed, but {winner} was cleaner when it mattered."

]


# =========================================================
# PLAYER REACTION BANKS
# =========================================================

QB_ELITE_REACTIONS = [

    "{player} was operating at an elite level. {yards} passing yards, {tds} touchdowns and only {ints} interceptions is the type of quarterback performance that changes games.",

    "{player} controlled this offense from the quarterback position. With {yards} yards and {tds} touchdowns, the defense never found a comfortable answer.",

    "That was quarterback excellence from {player}. The production was there, the decisions were there and the results followed.",

    "{player} was dealing. When your quarterback gives you {yards} yards and {tds} touchdowns, you expect to win football games.",

    "{player} looked completely comfortable running the offense. That was a high-level performance.",

    "If you're looking for the reason this offense was successful, start with {player}. He dictated the game."

]


QB_BAD_REACTIONS = [

    "{player} has to be better than this. A quarterback cannot put the offense in danger with {ints} interceptions and expect the rest of the team to constantly rescue him.",

    "I'm putting a lot of this on {player}. The quarterback has too much responsibility to play this poorly.",

    "{player} had a rough day, and there is no way around it. The decision-making has to improve.",

    "When the quarterback struggles like {player} did, everybody on offense ends up playing uphill.",

    "This was not good enough from {player}. Turnovers and missed opportunities made life much harder than it needed to be.",

    "{player} is going to have to own this performance because the quarterback position demands better."

]


QB_SOLID_REACTIONS = [

    "{player} gave his team a steady performance and avoided becoming the reason they lost.",

    "This wasn't a historic quarterback game, but {player} did enough to keep the offense functioning.",

    "{player} managed the game well and made enough throws when they were needed.",

    "You can win plenty of football games with the kind of performance {player} delivered.",

    "{player} wasn't flawless, but he gave the offense stability.",

    "There were good moments and things to clean up, but {player} kept the offense moving."

]


RUSHING_REACTIONS = [

    "{player} punished this defense on the ground. {yards} rushing yards and {tds} touchdowns made him one of the biggest reasons his offense succeeded.",

    "The defense knew {player} was getting the football and still couldn't consistently stop him.",

    "{player} took over the running game. Once he found a rhythm, the defense had a serious problem.",

    "{player} ran with purpose all game long and finished with {yards} yards on the ground.",

    "That was a physical rushing performance from {player}. He kept putting the offense in favorable situations.",

    "{player} made the ground game matter, and that changed the way the defense had to play."

]


RECEIVING_REACTIONS = [

    "{player} was a nightmare to cover. {yards} receiving yards and {tds} touchdowns tells you exactly how much damage he did.",

    "Every time the offense needed a big play, {player} seemed to be involved.",

    "{player} completely changed this game as a receiver. The defense never found a consistent answer.",

    "{player} put together a huge receiving performance and forced the secondary to account for him on every snap.",

    "That was a takeover game from {player}. {yards} yards through the air is serious production.",

    "{player} made life miserable for the secondary and delivered whenever his number was called."

]


DEFENSE_REACTIONS = [

    "{player} was everywhere defensively. {sacks} sacks and {ints} interceptions is impact football.",

    "{player} changed possessions and disrupted the offense all game long.",

    "That was a defensive takeover from {player}. He kept showing up around the football.",

    "{player} made the offense account for him on every important snap.",

    "Defense is about creating problems, and {player} created plenty of them.",

    "{player} delivered the kind of defensive performance coaches will replay in the film room all week."

]


# =========================================================
# WEEKLY DATA HELPERS
# =========================================================

def weekly_file(
    season_type,
    week_number,
    stat_type
):

    return os.path.join(
        DATA_DIR,
        "weekly",
        season_type,
        f"week_{week_number}",
        f"{stat_type}.json"
    )


def load_weekly_data(
    season_type,
    week_number,
    stat_type
):

    path = weekly_file(
        season_type,
        week_number,
        stat_type
    )

    if not os.path.exists(
        path
    ):
        return None

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(
                f
            )

    except Exception:

        return None


# =========================================================
# GENERIC STAT FIELD READER
# =========================================================

def stat_value(
    record,
    possible_keys,
    default=0
):

    value = first_value(
        record,
        possible_keys
    )

    if value is None:
        return default

    try:

        if isinstance(
            value,
            float
        ):
            return value

        return int(
            value
        )

    except Exception:

        try:
            return float(
                value
            )

        except Exception:
            return default


def stat_player_name(
    record
):

    return detect_player_name(
        record
    )


# =========================================================
# TEAM LOOKUP BY ID
# =========================================================

def team_by_id(
    team_id
):

    team_map = get_team_map()

    return team_map.get(
        str(team_id)
    )


def safe_team_name(
    team_id
):

    team = team_by_id(
        team_id
    )

    if not team:
        return (
            f"Team {team_id}"
        )

    return (
        team.get(
            "name"
        )
        or team.get(
            "abbr"
        )
        or f"Team {team_id}"
    )


def safe_team_overall(
    team_id
):

    team = team_by_id(
        team_id
    )

    if not team:
        return None

    try:
        return int(
            team.get(
                "overall"
            )
        )

    except Exception:
        return None


# =========================================================
# GAME COMPLETION CHECK
# =========================================================

def game_looks_completed(
    game
):

    away_score = (
        game.get(
            "awayScore",
            0
        )
        or 0
    )

    home_score = (
        game.get(
            "homeScore",
            0
        )
        or 0
    )

    # We know current unplayed games
    # are coming through as 0-0.
    if (
        away_score != 0
        or home_score != 0
    ):
        return True

    return False


# =========================================================
# CLASSIFY GAME STORY
# =========================================================

def classify_game_story(
    game
):

    away_id = game.get(
        "awayTeamId"
    )

    home_id = game.get(
        "homeTeamId"
    )

    away_score = int(
        game.get(
            "awayScore",
            0
        )
        or 0
    )

    home_score = int(
        game.get(
            "homeScore",
            0
        )
        or 0
    )

    away_name = (
        safe_team_name(
            away_id
        )
    )

    home_name = (
        safe_team_name(
            home_id
        )
    )

    away_ovr = (
        safe_team_overall(
            away_id
        )
    )

    home_ovr = (
        safe_team_overall(
            home_id
        )
    )

    if (
        away_score
        ==
        home_score
    ):

        return {
            "story_type":
                "tie",

            "winner":
                None,

            "loser":
                None,

            "margin":
                0,

            "away":
                away_name,

            "home":
                home_name,

            "away_score":
                away_score,

            "home_score":
                home_score
        }

    if (
        away_score
        >
        home_score
    ):

        winner = (
            away_name
        )

        loser = (
            home_name
        )

        winner_score = (
            away_score
        )

        loser_score = (
            home_score
        )

        winner_ovr = (
            away_ovr
        )

        loser_ovr = (
            home_ovr
        )

    else:

        winner = (
            home_name
        )

        loser = (
            away_name
        )

        winner_score = (
            home_score
        )

        loser_score = (
            away_score
        )

        winner_ovr = (
            home_ovr
        )

        loser_ovr = (
            away_ovr
        )

    margin = (
        winner_score
        -
        loser_score
    )

    upset = False

    if (
        winner_ovr is not None
        and loser_ovr is not None
        and winner_ovr
        <
        loser_ovr
    ):

        upset = True

    if margin >= 21:

        story_type = (
            "blowout"
        )

    elif upset:

        story_type = (
            "upset"
        )

    elif margin <= 3:

        story_type = (
            "close_game"
        )

    else:

        story_type = (
            "normal_win"
        )

    return {

        "story_type":
            story_type,

        "winner":
            winner,

        "loser":
            loser,

        "margin":
            margin,

        "winner_score":
            winner_score,

        "loser_score":
            loser_score,

        "winner_ovr":
            winner_ovr,

        "loser_ovr":
            loser_ovr,

        "away":
            away_name,

        "home":
            home_name,

        "away_score":
            away_score,

        "home_score":
            home_score,

        "upset":
            upset
    }


# =========================================================
# GAME HEADLINES
# =========================================================

def make_game_headline(
    story
):

    winner = story.get(
        "winner"
    )

    loser = story.get(
        "loser"
    )

    story_type = story.get(
        "story_type"
    )

    if story_type == "blowout":

        options = [

            f"{winner} sends a message in dominant win",

            f"{winner} overwhelms {loser}",

            f"{winner} leaves no doubt against {loser}",

            f"{loser} has no answers for {winner}",

            f"{winner} dominates from start to finish"
        ]

    elif story_type == "upset":

        options = [

            f"{winner} shocks {loser}",

            f"{winner} flips the script against {loser}",

            f"Ratings don't matter as {winner} beats {loser}",

            f"{winner} pulls off the upset",

            f"{loser} stunned by {winner}"
        ]

    elif story_type == "close_game":

        options = [

            f"{winner} survives a thriller",

            f"{winner} escapes against {loser}",

            f"{winner} delivers in the clutch",

            f"{loser} falls just short",

            f"{winner} wins a nail-biter"
        ]

    else:

        options = [

            f"{winner} handles business",

            f"{winner} gets the job done",

            f"{winner} earns the win over {loser}",

            f"{winner} proves to be the better team",

            f"{winner} takes care of {loser}"
        ]

    key = (
        f"{winner}-"
        f"{loser}-"
        f"{story.get('winner_score')}-"
        f"{story.get('loser_score')}"
    )

    return (
        unique_analyst_choice(
            "game_headlines",
            options,
            key
        )
    )


# =========================================================
# BUILD FULL GAME TAKE
# =========================================================

def build_game_take(
    story,
    key
):

    winner = story.get(
        "winner"
    )

    loser = story.get(
        "loser"
    )

    story_type = story.get(
        "story_type"
    )

    opening = (
        unique_analyst_choice(
            "game_opening",
            ANALYST_OPENINGS,
            key
        )
    )

    if story_type == "blowout":

        body_template = (
            unique_analyst_choice(
                "blowout_body",
                BLOWOUT_REACTIONS,
                key
            )
        )

        closer = (
            unique_analyst_choice(
                "positive_closer",
                ANALYST_POSITIVE_CLOSERS,
                key
            )
        )

    elif story_type == "upset":

        body_template = (
            unique_analyst_choice(
                "upset_body",
                UPSET_REACTIONS,
                key
            )
        )

        closer = (
            unique_analyst_choice(
                "upset_closer",
                [
                    "This league just got a lot more interesting.",

                    "Anybody overlooking this team needs to reconsider immediately.",

                    "That is how you earn respect when nobody expects you to win.",

                    "The ratings said one thing, but the football field said another.",

                    "You better remember this result the next time somebody calls this team an easy matchup."
                ],
                key
            )
        )

    elif story_type == "close_game":

        body_template = (
            unique_analyst_choice(
                "close_body",
                CLOSE_GAME_REACTIONS,
                key
            )
        )

        closer = (
            unique_analyst_choice(
                "close_closer",
                [
                    "Games like this reveal who can handle pressure.",

                    "That final margin shows just how important every mistake became.",

                    "Both teams will find plenty to study when they watch the film.",

                    "The winner gets to celebrate, but neither side can afford to ignore what happened.",

                    "This is the kind of game that can shape confidence going forward."
                ],
                key
            )
        )

    else:

        body_template = (
            unique_analyst_choice(
                "normal_win_body",
                NORMAL_WIN_REACTIONS,
                key
            )
        )

        closer = (
            unique_analyst_choice(
                "normal_win_closer",
                ANALYST_POSITIVE_CLOSERS,
                key
            )
        )

    body = body_template.format(
        winner=winner,
        loser=loser
    )

    return (
        f"{opening} "
        f"{body} "
        f"{closer}"
    )


# =========================================================
# GAME REACTION ENDPOINT
# =========================================================

@app.route(
    "/analyst/reactions/<season_type>/<int:week_number>"
)
def analyst_game_reactions(
    season_type,
    week_number
):

    schedule_data = (
        load_weekly_data(
            season_type,
            week_number,
            "schedules"
        )
    )

    if not schedule_data:

        return jsonify({

            "season_type":
                season_type,

            "week":
                week_number,

            "status":
                "waiting",

            "message":
                (
                    "No Snallabot "
                    "schedule export "
                    "has been received "
                    "for this week yet."
                ),

            "reactions":
                []
        })

    games = (
        schedule_data.get(
            "gameScheduleInfoList",
            []
        )
    )

    reactions = []

    for game in games:

        if not game_looks_completed(
            game
        ):
            continue

        story = (
            classify_game_story(
                game
            )
        )

        if (
            story.get(
                "story_type"
            )
            ==
            "tie"
        ):

            continue

        key = (
            f"{season_type}-"
            f"{week_number}-"
            f"{game.get('scheduleId')}"
        )

        headline = (
            make_game_headline(
                story
            )
        )

        take = (
            build_game_take(
                story,
                key
            )
        )

        reactions.append({

            "schedule_id":
                game.get(
                    "scheduleId"
                ),

            "game":
                (
                    f"{story['away']} "
                    f"{story['away_score']}"
                    f", "
                    f"{story['home']} "
                    f"{story['home_score']}"
                ),

            "story_type":
                story[
                    "story_type"
                ],

            "headline":
                headline,

            "winner":
                story[
                    "winner"
                ],

            "loser":
                story[
                    "loser"
                ],

            "margin":
                story[
                    "margin"
                ],

            "winner_ovr":
                story.get(
                    "winner_ovr"
                ),

            "loser_ovr":
                story.get(
                    "loser_ovr"
                ),

            "upset":
                story.get(
                    "upset",
                    False
                ),

            "analyst":
                (
                    "Project Madden "
                    "First Take"
                ),

            "analyst_take":
                take
        })

    return jsonify({

        "season_type":
            season_type,

        "week":
            week_number,

        "completed_games_found":
            len(
                reactions
            ),

        "reactions":
            reactions
    })


# =========================================================
# PLAYER STAT RECORDS
# =========================================================

def extract_stat_records(
    data
):

    if not data:
        return []

    records = (
        recursive_records(
            data
        )
    )

    useful = []

    for record in records:

        name = (
            stat_player_name(
                record
            )
        )

        if name:

            useful.append(
                record
            )

    return useful


# =========================================================
# PASSING ANALYSIS
# =========================================================

def passing_reactions(
    data,
    season_type,
    week_number
):

    records = (
        extract_stat_records(
            data
        )
    )

    results = []

    for record in records:

        player = (
            stat_player_name(
                record
            )
        )

        yards = stat_value(
            record,
            [
                "passYds",
                "passingYards",
                "passYards",
                "yards",
                "pass_yds"
            ]
        )

        tds = stat_value(
            record,
            [
                "passTDs",
                "passingTDs",
                "passTouchdowns",
                "tds",
                "pass_tds"
            ]
        )

        ints = stat_value(
            record,
            [
                "passInts",
                "passingInts",
                "interceptions",
                "ints",
                "pass_ints"
            ]
        )

        if (
            yards <= 0
            and tds <= 0
            and ints <= 0
        ):
            continue

        key = (
            f"pass-"
            f"{season_type}-"
            f"{week_number}-"
            f"{player}-"
            f"{yards}-"
            f"{tds}-"
            f"{ints}"
        )

        if (
            yards >= 300
            and tds >= 3
            and ints <= 1
        ):

            story_type = (
                "elite_qb_game"
            )

            template = (
                unique_analyst_choice(
                    "elite_qb",
                    QB_ELITE_REACTIONS,
                    key
                )
            )

        elif (
            ints >= 3
            or (
                ints >= 2
                and tds == 0
            )
        ):

            story_type = (
                "qb_disaster"
            )

            template = (
                unique_analyst_choice(
                    "bad_qb",
                    QB_BAD_REACTIONS,
                    key
                )
            )

        elif (
            yards >= 220
            or tds >= 2
        ):

            story_type = (
                "solid_qb_game"
            )

            template = (
                unique_analyst_choice(
                    "solid_qb",
                    QB_SOLID_REACTIONS,
                    key
                )
            )

        else:
            continue

        text = template.format(
            player=player,
            yards=yards,
            tds=tds,
            ints=ints
        )

        results.append({

            "player":
                player,

            "category":
                "passing",

            "story_type":
                story_type,

            "stats": {
                "yards":
                    yards,

                "touchdowns":
                    tds,

                "interceptions":
                    ints
            },

            "analyst_take":
                text
        })

    return results


# =========================================================
# RUSHING ANALYSIS
# =========================================================

def rushing_reactions(
    data,
    season_type,
    week_number
):

    records = (
        extract_stat_records(
            data
        )
    )

    results = []

    for record in records:

        player = (
            stat_player_name(
                record
            )
        )

        yards = stat_value(
            record,
            [
                "rushYds",
                "rushingYards",
                "rushYards",
                "rush_yds"
            ]
        )

        tds = stat_value(
            record,
            [
                "rushTDs",
                "rushingTDs",
                "rushTouchdowns",
                "rush_tds"
            ]
        )

        if (
            yards < 100
            and tds < 2
        ):
            continue

        key = (
            f"rush-"
            f"{season_type}-"
            f"{week_number}-"
            f"{player}-"
            f"{yards}-"
            f"{tds}"
        )

        template = (
            unique_analyst_choice(
                "rushing_star",
                RUSHING_REACTIONS,
                key
            )
        )

        results.append({

            "player":
                player,

            "category":
                "rushing",

            "story_type":
                "rushing_takeover",

            "stats": {
                "yards":
                    yards,

                "touchdowns":
                    tds
            },

            "analyst_take":
                template.format(
                    player=player,
                    yards=yards,
                    tds=tds
                )
        })

    return results


# =========================================================
# RECEIVING ANALYSIS
# =========================================================

def receiving_reactions(
    data,
    season_type,
    week_number
):

    records = (
        extract_stat_records(
            data
        )
    )

    results = []

    for record in records:

        player = (
            stat_player_name(
                record
            )
        )

        yards = stat_value(
            record,
            [
                "recYds",
                "receivingYards",
                "receiveYards",
                "rec_yds"
            ]
        )

        tds = stat_value(
            record,
            [
                "recTDs",
                "receivingTDs",
                "receivingTouchdowns",
                "rec_tds"
            ]
        )

        if (
            yards < 100
            and tds < 2
        ):
            continue

        key = (
            f"rec-"
            f"{season_type}-"
            f"{week_number}-"
            f"{player}-"
            f"{yards}-"
            f"{tds}"
        )

        template = (
            unique_analyst_choice(
                "receiving_star",
                RECEIVING_REACTIONS,
                key
            )
        )

        results.append({

            "player":
                player,

            "category":
                "receiving",

            "story_type":
                "receiver_takeover",

            "stats": {
                "yards":
                    yards,

                "touchdowns":
                    tds
            },

            "analyst_take":
                template.format(
                    player=player,
                    yards=yards,
                    tds=tds
                )
        })

    return results


# =========================================================
# DEFENSIVE ANALYSIS
# =========================================================

def defense_reactions(
    data,
    season_type,
    week_number
):

    records = (
        extract_stat_records(
            data
        )
    )

    results = []

    for record in records:

        player = (
            stat_player_name(
                record
            )
        )

        sacks = stat_value(
            record,
            [
                "defSacks",
                "sacks",
                "sackCount",
                "def_sacks"
            ]
        )

        ints = stat_value(
            record,
            [
                "defInts",
                "interceptions",
                "defensiveInterceptions",
                "def_ints"
            ]
        )

        forced_fumbles = (
            stat_value(
                record,
                [
                    "forcedFumbles",
                    "fumblesForced",
                    "ff"
                ]
            )
        )

        if (
            sacks < 2
            and ints < 1
            and forced_fumbles < 2
        ):
            continue

        key = (
            f"def-"
            f"{season_type}-"
            f"{week_number}-"
            f"{player}-"
            f"{sacks}-"
            f"{ints}-"
            f"{forced_fumbles}"
        )

        template = (
            unique_analyst_choice(
                "defensive_star",
                DEFENSE_REACTIONS,
                key
            )
        )

        results.append({

            "player":
                player,

            "category":
                "defense",

            "story_type":
                "defensive_takeover",

            "stats": {
                "sacks":
                    sacks,

                "interceptions":
                    ints,

                "forced_fumbles":
                    forced_fumbles
            },

            "analyst_take":
                template.format(
                    player=player,
                    sacks=sacks,
                    ints=ints
                )
        })

    return results


# =========================================================
# PLAYER REACTION ENDPOINT
# =========================================================

@app.route(
    "/analyst/players/<season_type>/<int:week_number>"
)
def analyst_player_reactions(
    season_type,
    week_number
):

    passing_data = (
        load_weekly_data(
            season_type,
            week_number,
            "passing"
        )
    )

    rushing_data = (
        load_weekly_data(
            season_type,
            week_number,
            "rushing"
        )
    )

    receiving_data = (
        load_weekly_data(
            season_type,
            week_number,
            "receiving"
        )
    )

    defense_data = (
        load_weekly_data(
            season_type,
            week_number,
            "defense"
        )
    )

    reactions = []

    if passing_data:

        reactions.extend(
            passing_reactions(
                passing_data,
                season_type,
                week_number
            )
        )

    if rushing_data:

        reactions.extend(
            rushing_reactions(
                rushing_data,
                season_type,
                week_number
            )
        )

    if receiving_data:

        reactions.extend(
            receiving_reactions(
                receiving_data,
                season_type,
                week_number
            )
        )

    if defense_data:

        reactions.extend(
            defense_reactions(
                defense_data,
                season_type,
                week_number
            )
        )

    return jsonify({

        "season_type":
            season_type,

        "week":
            week_number,

        "files_received": {

            "passing":
                passing_data
                is not None,

            "rushing":
                rushing_data
                is not None,

            "receiving":
                receiving_data
                is not None,

            "defense":
                defense_data
                is not None
        },

        "reaction_count":
            len(
                reactions
            ),

        "status":
            (
                "ready"
                if reactions
                else
                "waiting_for_player_performances"
            ),

        "reactions":
            reactions
    })


# =========================================================
# ANALYST WEEKLY SHOW
# Combines games + players
# =========================================================

@app.route(
    "/analyst/show/<season_type>/<int:week_number>"
)
def analyst_weekly_show(
    season_type,
    week_number
):

    schedule_data = (
        load_weekly_data(
            season_type,
            week_number,
            "schedules"
        )
    )

    game_segments = []

    if schedule_data:

        games = (
            schedule_data.get(
                "gameScheduleInfoList",
                []
            )
        )

        for game in games:

            if not (
                game_looks_completed(
                    game
                )
            ):
                continue

            story = (
                classify_game_story(
                    game
                )
            )

            if (
                story.get(
                    "story_type"
                )
                ==
                "tie"
            ):
                continue

            key = (
                f"show-"
                f"{season_type}-"
                f"{week_number}-"
                f"{game.get('scheduleId')}"
            )

            game_segments.append({

                "headline":
                    make_game_headline(
                        story
                    ),

                "game":
                    (
                        f"{story['away']} "
                        f"{story['away_score']}"
                        f", "
                        f"{story['home']} "
                        f"{story['home_score']}"
                    ),

                "story_type":
                    story[
                        "story_type"
                    ],

                "script":
                    build_game_take(
                        story,
                        key
                    )
            })

    passing_data = (
        load_weekly_data(
            season_type,
            week_number,
            "passing"
        )
    )

    rushing_data = (
        load_weekly_data(
            season_type,
            week_number,
            "rushing"
        )
    )

    receiving_data = (
        load_weekly_data(
            season_type,
            week_number,
            "receiving"
        )
    )

    defense_data = (
        load_weekly_data(
            season_type,
            week_number,
            "defense"
        )
    )

    player_segments = []

    if passing_data:

        player_segments.extend(
            passing_reactions(
                passing_data,
                season_type,
                week_number
            )
        )

    if rushing_data:

        player_segments.extend(
            rushing_reactions(
                rushing_data,
                season_type,
                week_number
            )
        )

    if receiving_data:

        player_segments.extend(
            receiving_reactions(
                receiving_data,
                season_type,
                week_number
            )
        )

    if defense_data:

        player_segments.extend(
            defense_reactions(
                defense_data,
                season_type,
                week_number
            )
        )

    return jsonify({

        "show":
            "Project Madden First Take",

        "analyst":
            (
                "Project Madden "
                "Debate Analyst"
            ),

        "season_type":
            season_type,

        "week":
            week_number,

        "game_segments":
            game_segments,

        "player_segments":
            player_segments,

        "total_segments":
            (
                len(
                    game_segments
                )
                +
                len(
                    player_segments
                )
            )
    })
