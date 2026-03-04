from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
import os
import random

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev"
socketio = SocketIO(app, async_mode="threading")

rooms = {}

# ==============================
# CLASSES DE HEROI
# ==============================

HERO_CLASSES = {
    "Guerreiro": {"hp": 32},
    "Mago": {"hp": 32, "bonus_spell": 2},
    "Arqueiro": {"hp": 32},
    "Paladino": {"hp": 32, "bonus_defense": 2},
    "Ladino": {"hp": 32},
}

# ==============================
# AFINIDADES
# ==============================

AFFINITIES = {
    "Guerreiro": {
        "weapon_bonus": ["Machado"],
        "armor_bonus": ["Pesada"],
        "penalty": ["Adaga", "Espada", "Leve", "Cajado", "Arco"],
    },
    "Mago": {
        "weapon_bonus": ["Cajado"],
        "armor_bonus": ["Leve"],
        "penalty": ["Pesada", "Media", "Machado", "Adaga", "Espada", "Arco"],
    },
    "Arqueiro": {
        "weapon_bonus": ["Arco"],
        "armor_bonus": ["Leve"],
        "penalty": ["Pesada", "Media", "Machado", "Espada", "Cajado"],
    },
    "Paladino": {
        "weapon_bonus": ["Espada"],
        "armor_bonus": ["Pesada"],
        "penalty": ["Adaga", "Leve", "Machado", "Cajado", "Arco"],
    },
    "Ladino": {
        "weapon_bonus": ["Adaga"],
        "armor_bonus": ["Leve"],
        "penalty": ["Pesada", "Machado", "Espada", "Cajado", "Arco"],
    },
}


@app.route("/")
def index():
    return render_template("index.html")


def create_deck():
    weapons = [
        {"name": "Espada", "type": "weapon", "damage": 5},
        {"name": "Machado", "type": "weapon", "damage": 5},
        {"name": "Adaga", "type": "weapon", "damage": 5},
        {"name": "Cajado", "type": "weapon", "damage": 5},
        {"name": "Arco", "type": "weapon", "damage": 5},
    ]

    armors = [
        {"name": "Leve", "type": "armor", "defense": 9},
        {"name": "Media", "type": "armor", "defense": 9},
        {"name": "Pesada", "type": "armor", "defense": 9},
    ]

    potions = [
        {"name": "Cura Pequena", "type": "potion", "heal": 5},
        {"name": "Cura Grande", "type": "potion", "heal": 10},
    ]

    spells = [
        {"name": "Bola de Fogo", "type": "spell", "effect": "fireball", "damage": 8},
        {"name": "Relampago", "type": "spell", "effect": "lightning", "max_roll": 10},
        {"name": "Gelo", "type": "spell", "effect": "freeze", "turns": 1},
        {"name": "Pele de Ferro", "type": "spell", "effect": "iron_skin", "defense": 4},
    ]

    deck = weapons * 3 + armors * 3 + potions * 3 + spells * 3
    random.shuffle(deck)
    return deck


def create_empty_room():
    return {
        "players": [],
        "started": False,
        "turn": 0,
        "hp": [0, 0],
        "max_hp": [0, 0],
        "deck": create_deck(),
        "discard": [],
        "hands": [[], []],
        "weapons": [None, None],
        "armors": [None, None],
        "draw_used": [False, False],
        "frozen_turns": [0, 0],
        "temp_defense": [0, 0],
        "temp_defense_expire": [False, False],
        "classes": [None, None],
        "last_roll": None,
        "last_damage": None,
        "attack_breakdown": None,
        "last_actor": None,
        "last_action": "Aguardando segundo jogador.",
    }


def draw_from_room(room):
    if not room["deck"] and room["discard"]:
        room["deck"].extend(room["discard"])
        room["discard"].clear()
        random.shuffle(room["deck"])

    if not room["deck"]:
        return None

    return room["deck"].pop()


def format_signed(value):
    if value >= 0:
        return f"+{value}"
    return str(value)


def must_discard(room, player_index):
    return len(room["hands"][player_index]) > 5


def is_frozen(room, player_index):
    return room["frozen_turns"][player_index] > 0


def get_spell_bonus(room, player_index):
    player_class = room["classes"][player_index]
    weapon = room["weapons"][player_index]
    if player_class != "Mago":
        return 0
    if not weapon or weapon["name"] != "Cajado":
        return 0
    return HERO_CLASSES[player_class].get("bonus_spell", 0)


def start_player_turn(room, player_index):
    if room["temp_defense_expire"][player_index]:
        room["temp_defense"][player_index] = 0
        room["temp_defense_expire"][player_index] = False


def pass_turn(room, next_player):
    room["turn"] = next_player
    room["draw_used"] = [False, False]
    start_player_turn(room, next_player)


def build_player_state(room_id, room, player_index):
    opponent = 1 - player_index

    return {
        "room_id": room_id,
        "player_index": player_index,
        "turn": room["turn"],
        "classes": room["classes"],
        "hp": room["hp"],
        "max_hp": room["max_hp"],
        "hand": room["hands"][player_index],
        "opponent_hand_size": len(room["hands"][opponent]),
        "weapon": room["weapons"][player_index],
        "armor": room["armors"][player_index],
        "opponent_weapon": room["weapons"][opponent],
        "opponent_armor": room["armors"][opponent],
        "is_frozen": is_frozen(room, player_index),
        "frozen_turns": room["frozen_turns"][player_index],
        "opponent_frozen_turns": room["frozen_turns"][opponent],
        "temp_defense": room["temp_defense"][player_index],
        "opponent_temp_defense": room["temp_defense"][opponent],
        "must_discard": must_discard(room, player_index),
        "draw_used": room["draw_used"][player_index],
        "deck_count": len(room["deck"]),
        "last_roll": room["last_roll"],
        "last_damage": room["last_damage"],
        "attack_breakdown": room["attack_breakdown"],
        "last_actor": room["last_actor"],
        "last_action": room["last_action"],
    }


def emit_room_state(room_id, event_name="state"):
    room = rooms.get(room_id)
    if not room:
        return

    for index, sid in enumerate(room["players"]):
        socketio.emit(
            event_name,
            {
                "room_id": room_id,
                "state": build_player_state(room_id, room, index),
                "affinities": AFFINITIES,
            },
            room=sid,
        )


def send_error(message):
    emit("error_message", {"message": message}, room=request.sid)


def get_room_and_player(data):
    room_id = str((data or {}).get("room", "")).strip()
    if not room_id:
        send_error("Informe o codigo da sala.")
        return None, None, None

    room = rooms.get(room_id)
    if room is None:
        send_error("Sala nao encontrada.")
        return None, None, None

    if request.sid not in room["players"]:
        send_error("Voce nao pertence a esta sala.")
        return None, None, None

    return room_id, room, room["players"].index(request.sid)


def start_match(room_id):
    room = rooms[room_id]
    room["started"] = True
    room["turn"] = random.randint(0, 1)
    room["draw_used"] = [False, False]
    room["last_roll"] = None
    room["last_damage"] = None
    room["attack_breakdown"] = None
    room["last_actor"] = None

    class_pool = list(HERO_CLASSES.keys())
    random.shuffle(class_pool)
    room["classes"] = [class_pool[0], class_pool[1]]

    room["max_hp"] = [
        HERO_CLASSES[room["classes"][0]]["hp"],
        HERO_CLASSES[room["classes"][1]]["hp"],
    ]
    room["hp"] = room["max_hp"][:]

    room["hands"] = [[], []]
    room["weapons"] = [None, None]
    room["armors"] = [None, None]
    room["frozen_turns"] = [0, 0]
    room["temp_defense"] = [0, 0]
    room["temp_defense_expire"] = [False, False]

    for i in range(2):
        for _ in range(5):
            card = draw_from_room(room)
            if card:
                room["hands"][i].append(card)

    room["last_action"] = f"Partida iniciada. Jogador {room['turn'] + 1} comeca."
    emit_room_state(room_id, event_name="start_game")


@socketio.on("create_room")
def create_room_handler():
    room_id = str(random.randint(1000, 9999))
    while room_id in rooms:
        room_id = str(random.randint(1000, 9999))

    rooms[room_id] = create_empty_room()
    rooms[room_id]["players"].append(request.sid)

    join_room(room_id)
    emit("room_created", {"room": room_id}, room=request.sid)
    emit(
        "info_message",
        {"message": "Sala criada. Compartilhe o codigo para o segundo jogador."},
        room=request.sid,
    )


@socketio.on("join_room")
def join_room_handler(data):
    room_id = str((data or {}).get("room", "")).strip()
    if not room_id:
        send_error("Informe o codigo da sala.")
        return

    room = rooms.get(room_id)
    if room is None:
        send_error("Sala nao encontrada.")
        return

    if request.sid in room["players"]:
        join_room(room_id)
        emit("room_joined", {"room": room_id}, room=request.sid)
        return

    if len(room["players"]) >= 2:
        send_error("Sala cheia.")
        return

    room["players"].append(request.sid)
    join_room(room_id)
    emit("room_joined", {"room": room_id}, room=request.sid)

    socketio.emit(
        "info_message",
        {"message": "Segundo jogador entrou. A partida vai comecar."},
        room=room_id,
    )

    if len(room["players"]) == 2:
        start_match(room_id)


@socketio.on("draw_card")
def draw_card_handler(data):
    room_id, room, player_index = get_room_and_player(data)
    if room is None:
        return

    if not room["started"]:
        send_error("A partida ainda nao comecou.")
        return

    if room["turn"] != player_index:
        send_error("Nao e seu turno.")
        return

    if is_frozen(room, player_index):
        send_error("Voce esta congelado e so pode encerrar o turno.")
        return

    if must_discard(room, player_index):
        send_error("Voce esta com mais de 5 cartas e precisa descartar.")
        return

    if room["draw_used"][player_index]:
        send_error("Voce ja comprou carta neste turno.")
        return

    card = draw_from_room(room)
    if not card:
        send_error("Nao ha cartas no baralho.")
        return

    room["hands"][player_index].append(card)
    room["draw_used"][player_index] = True
    room["last_actor"] = player_index
    hand_size = len(room["hands"][player_index])
    if hand_size > 5:
        room["last_action"] = (
            f"Jogador {player_index + 1} comprou 1 carta e precisa descartar ate 5."
        )
    else:
        room["last_action"] = f"Jogador {player_index + 1} comprou 1 carta."

    emit_room_state(room_id)


@socketio.on("discard_card")
def discard_card_handler(data):
    room_id, room, player_index = get_room_and_player(data)
    if room is None:
        return

    if not room["started"]:
        send_error("A partida ainda nao comecou.")
        return

    if room["turn"] != player_index:
        send_error("Nao e seu turno.")
        return

    if is_frozen(room, player_index):
        send_error("Voce esta congelado e so pode encerrar o turno.")
        return

    card_index = (data or {}).get("card_index")
    if not isinstance(card_index, int):
        send_error("Indice de carta invalido.")
        return

    if not (0 <= card_index < len(room["hands"][player_index])):
        send_error("Carta nao encontrada na mao.")
        return

    discarded = room["hands"][player_index].pop(card_index)
    room["discard"].append(discarded)
    room["last_actor"] = player_index
    room["last_action"] = f"Jogador {player_index + 1} descartou {discarded['name']}."

    emit_room_state(room_id)


@socketio.on("play_card")
def play_card_handler(data):
    room_id, room, player_index = get_room_and_player(data)
    if room is None:
        return

    if not room["started"]:
        send_error("A partida ainda nao comecou.")
        return

    if room["turn"] != player_index:
        send_error("Nao e seu turno.")
        return

    if is_frozen(room, player_index):
        send_error("Voce esta congelado e so pode encerrar o turno.")
        return

    if must_discard(room, player_index):
        send_error("Voce esta com mais de 5 cartas e precisa descartar.")
        return

    card_index = (data or {}).get("card_index")
    if not isinstance(card_index, int):
        send_error("Indice de carta invalido.")
        return

    if not (0 <= card_index < len(room["hands"][player_index])):
        send_error("Carta nao encontrada na mao.")
        return

    card = room["hands"][player_index].pop(card_index)
    opponent = 1 - player_index

    if card["type"] == "weapon":
        if room["weapons"][player_index] is not None:
            room["discard"].append(room["weapons"][player_index])
        room["weapons"][player_index] = card
        room["last_action"] = f"Jogador {player_index + 1} equipou arma {card['name']}."

    elif card["type"] == "armor":
        if room["armors"][player_index] is not None:
            room["discard"].append(room["armors"][player_index])
        room["armors"][player_index] = card
        room["last_action"] = f"Jogador {player_index + 1} equipou armadura {card['name']}."

    elif card["type"] == "potion":
        old_hp = room["hp"][player_index]
        heal = int(card.get("heal", 0))
        room["hp"][player_index] = min(room["max_hp"][player_index], old_hp + heal)
        real_heal = room["hp"][player_index] - old_hp
        room["discard"].append(card)
        room["last_action"] = (
            f"Jogador {player_index + 1} usou {card['name']} e curou {real_heal} HP."
        )

    elif card["type"] == "spell":
        spell_bonus = get_spell_bonus(room, player_index)
        effect = card.get("effect")
        caster_class = room["classes"][player_index]
        using_staff = spell_bonus > 0
        room["discard"].append(card)

        if effect == "fireball":
            base_damage = int(card.get("damage", 8))
            damage = base_damage + spell_bonus
            room["hp"][opponent] = max(0, room["hp"][opponent] - damage)
            room["last_roll"] = None
            room["last_damage"] = damage
            room["attack_breakdown"] = [
                {"label": "Tipo", "value": "Spell"},
                {"label": "Conjurador", "value": f"Jogador {player_index + 1} ({caster_class})"},
                {"label": "Alvo", "value": f"Jogador {opponent + 1}"},
                {"label": "Carta", "value": card["name"]},
                {"label": "Ignora armadura", "value": "Sim"},
                {"label": "Bonus do Mago com Cajado", "value": format_signed(spell_bonus)},
                {"label": "Calculo", "value": f"{base_damage} {format_signed(spell_bonus)} = {damage}"},
                {"label": "Dano final", "value": str(damage)},
            ]
            room["last_action"] = (
                f"Jogador {player_index + 1} conjurou Bola de Fogo e causou {damage} de dano."
            )

        elif effect == "lightning":
            roll = random.randint(1, int(card.get("max_roll", 10)))
            damage = roll + spell_bonus
            room["hp"][opponent] = max(0, room["hp"][opponent] - damage)
            room["last_roll"] = roll
            room["last_damage"] = damage
            room["attack_breakdown"] = [
                {"label": "Tipo", "value": "Spell"},
                {"label": "Conjurador", "value": f"Jogador {player_index + 1} ({caster_class})"},
                {"label": "Alvo", "value": f"Jogador {opponent + 1}"},
                {"label": "Carta", "value": card["name"]},
                {"label": "Ignora armadura", "value": "Sim"},
                {"label": "Rolagem", "value": f"1d10 = {roll}"},
                {"label": "Bonus do Mago com Cajado", "value": format_signed(spell_bonus)},
                {"label": "Calculo", "value": f"{roll} {format_signed(spell_bonus)} = {damage}"},
                {"label": "Dano final", "value": str(damage)},
            ]
            room["last_action"] = (
                f"Jogador {player_index + 1} conjurou Relampago e causou {damage} de dano."
            )

        elif effect == "freeze":
            freeze_turns = int(card.get("turns", 1)) + spell_bonus
            room["frozen_turns"][opponent] += freeze_turns
            room["last_roll"] = None
            room["last_damage"] = 0
            room["attack_breakdown"] = [
                {"label": "Tipo", "value": "Spell"},
                {"label": "Conjurador", "value": f"Jogador {player_index + 1} ({caster_class})"},
                {"label": "Alvo", "value": f"Jogador {opponent + 1}"},
                {"label": "Carta", "value": card["name"]},
                {"label": "Bonus do Mago com Cajado", "value": format_signed(spell_bonus)},
                {"label": "Turnos de congelamento aplicados", "value": str(freeze_turns)},
                {"label": "Total de turnos congelado no alvo", "value": str(room["frozen_turns"][opponent])},
                {"label": "Regra", "value": "Alvo nao pode comprar, atacar, jogar ou descartar; so encerrar turno."},
            ]
            room["last_action"] = (
                f"Jogador {player_index + 1} conjurou Gelo e congelou o oponente por {freeze_turns} turno(s)."
            )

        elif effect == "iron_skin":
            defense_bonus = int(card.get("defense", 4)) + spell_bonus
            room["temp_defense"][player_index] = defense_bonus
            room["temp_defense_expire"][player_index] = True
            room["last_roll"] = None
            room["last_damage"] = 0
            room["attack_breakdown"] = [
                {"label": "Tipo", "value": "Spell"},
                {"label": "Conjurador", "value": f"Jogador {player_index + 1} ({caster_class})"},
                {"label": "Carta", "value": card["name"]},
                {"label": "Bonus do Mago com Cajado", "value": format_signed(spell_bonus)},
                {"label": "Defesa temporaria aplicada", "value": str(defense_bonus)},
                {"label": "Duracao", "value": "Ate o inicio do seu proximo turno."},
            ]
            room["last_action"] = (
                f"Jogador {player_index + 1} conjurou Pele de Ferro e ganhou +{defense_bonus} de defesa temporaria."
            )

        else:
            room["last_action"] = f"Jogador {player_index + 1} jogou {card['name']}."
            if using_staff:
                room["last_action"] += " (Bonus de spell ativo)"

        if room["hp"][opponent] <= 0:
            room["last_actor"] = player_index
            emit_room_state(room_id)
            socketio.emit("game_over", {"winner_index": player_index}, room=room_id)
            return

    else:
        room["discard"].append(card)
        room["last_action"] = f"Jogador {player_index + 1} jogou {card['name']}."

    room["last_actor"] = player_index
    emit_room_state(room_id)


@socketio.on("end_turn")
def end_turn_handler(data):
    room_id, room, player_index = get_room_and_player(data)
    if room is None:
        return

    if not room["started"]:
        send_error("A partida ainda nao comecou.")
        return

    if room["turn"] != player_index:
        send_error("Nao e seu turno.")
        return

    if is_frozen(room, player_index):
        room["frozen_turns"][player_index] -= 1
        remaining = room["frozen_turns"][player_index]
        room["last_action"] = (
            f"Jogador {player_index + 1} estava congelado e encerrou o turno."
        )
        if remaining > 0:
            room["last_action"] += f" Restam {remaining} turno(s) congelado."
    else:
        if must_discard(room, player_index):
            send_error("Voce esta com mais de 5 cartas e precisa descartar.")
            return
        room["last_action"] = f"Jogador {player_index + 1} encerrou o turno."

    pass_turn(room, 1 - player_index)
    room["last_actor"] = player_index
    emit_room_state(room_id)


@socketio.on("attack")
def attack_handler(data):
    room_id, room, player_index = get_room_and_player(data)
    if room is None:
        return

    if not room["started"]:
        send_error("A partida ainda nao comecou.")
        return

    if room["turn"] != player_index:
        send_error("Nao e seu turno.")
        return

    if is_frozen(room, player_index):
        send_error("Voce esta congelado e so pode encerrar o turno.")
        return

    if must_discard(room, player_index):
        send_error("Voce esta com mais de 5 cartas e precisa descartar.")
        return

    opponent = 1 - player_index

    attacker_class = room["classes"][player_index]
    defender_class = room["classes"][opponent]
    attacker_affinity = AFFINITIES.get(attacker_class, {})
    defender_affinity = AFFINITIES.get(defender_class, {})

    attacker_weapon = room["weapons"][player_index]
    attacker_armor = room["armors"][player_index]
    defender_armor = room["armors"][opponent]

    attacker_weapon_name = attacker_weapon["name"] if attacker_weapon else None
    attacker_armor_name = attacker_armor["name"] if attacker_armor else None
    defender_armor_name = defender_armor["name"] if defender_armor else None

    disadvantage = False
    if attacker_weapon_name and attacker_weapon_name in attacker_affinity.get("penalty", []):
        disadvantage = True
    if attacker_armor_name and attacker_armor_name in attacker_affinity.get("penalty", []):
        disadvantage = True

    if disadvantage:
        roll_1 = random.randint(1, 20)
        roll_2 = random.randint(1, 20)
        roll = min(roll_1, roll_2)
        roll_text = f"{roll_1}, {roll_2} (desvantagem)"
    else:
        roll = random.randint(1, 20)
        roll_text = str(roll)

    weapon_base_damage = attacker_weapon["damage"] if attacker_weapon else 0
    weapon_affinity_bonus = (
        2
        if attacker_weapon_name
        and attacker_weapon_name in attacker_affinity.get("weapon_bonus", [])
        else 0
    )
    weapon_penalty = (
        -2
        if attacker_weapon_name and attacker_weapon_name in attacker_affinity.get("penalty", [])
        else 0
    )

    armor_attack_penalty = (
        -2
        if attacker_armor_name and attacker_armor_name in attacker_affinity.get("penalty", [])
        else 0
    )

    attack_total = (
        roll
        + weapon_base_damage
        + weapon_affinity_bonus
        + weapon_penalty
        + armor_attack_penalty
    )

    defender_armor_defense = defender_armor["defense"] if defender_armor else 0
    defense_class_bonus = HERO_CLASSES[defender_class].get("bonus_defense", 0)
    defense_temp_bonus = room["temp_defense"][opponent]
    defense_armor_affinity_bonus = (
        1
        if defender_armor_name and defender_armor_name in defender_affinity.get("armor_bonus", [])
        else 0
    )
    defense_armor_penalty = (
        -1
        if defender_armor_name and defender_armor_name in defender_affinity.get("penalty", [])
        else 0
    )

    defense_total = max(
        0,
        defense_class_bonus
        + defense_temp_bonus
        + defender_armor_defense
        + defense_armor_affinity_bonus
        + defense_armor_penalty,
    )

    base_damage = max(0, attack_total - defense_total)
    critical_bonus = 3 if roll == 20 else 0

    if roll == 1:
        damage = 0
    else:
        damage = base_damage + critical_bonus

    room["hp"][opponent] = max(0, room["hp"][opponent] - damage)
    room["last_roll"] = roll
    room["last_damage"] = damage

    attack_formula = (
        f"{roll} + {weapon_base_damage} {format_signed(weapon_affinity_bonus)} "
        f"{format_signed(weapon_penalty)} "
        f"{format_signed(armor_attack_penalty)} = {attack_total}"
    )
    defense_formula = (
        f"{defense_class_bonus} + {defense_temp_bonus} + {defender_armor_defense} "
        f"{format_signed(defense_armor_affinity_bonus)} {format_signed(defense_armor_penalty)} = {defense_total}"
    )
    if roll == 1:
        damage_formula = "Rolagem 1 no d20: falha critica, dano final = 0"
    else:
        damage_formula = f"max(0, {attack_total} - {defense_total})"
        if critical_bonus:
            damage_formula += f" {format_signed(critical_bonus)}"
        damage_formula += f" = {damage}"

    room["attack_breakdown"] = [
        {"label": "Atacante", "value": f"Jogador {player_index + 1} ({attacker_class})"},
        {"label": "Defensor", "value": f"Jogador {opponent + 1} ({defender_class})"},
        {"label": "Arma atacante", "value": attacker_weapon_name or "Nenhuma"},
        {"label": "Armadura atacante", "value": attacker_armor_name or "Nenhuma"},
        {"label": "Desvantagem", "value": "Sim" if disadvantage else "Nao"},
        {"label": "Rolagens", "value": roll_text},
        {"label": "Rolagem usada", "value": str(roll)},
        {"label": "Base da arma", "value": str(weapon_base_damage)},
        {"label": "Bonus afinidade arma", "value": format_signed(weapon_affinity_bonus)},
        {"label": "Penalidade de arma", "value": format_signed(weapon_penalty)},
        {"label": "Penalidade da armadura atacante", "value": format_signed(armor_attack_penalty)},
        {"label": "Ataque total", "value": attack_formula},
        {"label": "Armadura defensora", "value": defender_armor_name or "Nenhuma"},
        {"label": "Base da armadura defensora", "value": str(defender_armor_defense)},
        {"label": "Bonus de classe (defesa)", "value": format_signed(defense_class_bonus)},
        {"label": "Defesa temporaria", "value": format_signed(defense_temp_bonus)},
        {"label": "Bonus afinidade armadura", "value": format_signed(defense_armor_affinity_bonus)},
        {"label": "Penalidade de armadura", "value": format_signed(defense_armor_penalty)},
        {"label": "Defesa total", "value": defense_formula},
        {"label": "Bonus critico", "value": format_signed(critical_bonus)},
        {"label": "Calculo do dano final", "value": damage_formula},
    ]
    room["last_actor"] = player_index
    room["last_action"] = f"Jogador {player_index + 1} atacou e causou {damage} de dano."
    pass_turn(room, opponent)

    emit_room_state(room_id)

    if room["hp"][opponent] <= 0:
        socketio.emit("game_over", {"winner_index": player_index}, room=room_id)


@socketio.on("disconnect")
def disconnect_handler():
    for room_id, room in list(rooms.items()):
        if request.sid not in room["players"]:
            continue

        room["players"] = [sid for sid in room["players"] if sid != request.sid]

        if room["players"]:
            socketio.emit(
                "opponent_left",
                {"message": "Oponente desconectou. Esta sala foi encerrada."},
                room=room["players"][0],
            )

        rooms.pop(room_id, None)
        break


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    socketio.run(app, host="0.0.0.0", port=port)
