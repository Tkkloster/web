from django.shortcuts import get_object_or_404
from rest_framework import viewsets, serializers
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from .models import User, Game, Card, Chug, PlayerStat, GamePlayer, Season
from .ranking import RANKINGS
from .facebook import post_to_page
from .serializers import (
    UserSerializer,
    GameSerializer,
    GameSerializerWithPlayerStats,
    CreateGameSerializer,
    PlayerStatSerializer,
)


class CustomAuthToken(ObtainAuthToken):
    def post(self, request, *args, **kwargs):
        try:
            response = super().post(request, *args, **kwargs)
            token = Token.objects.get(key=response.data["token"])
            user = token.user
            response.data["id"] = user.id
            response.data["image"] = request.build_absolute_uri(user.image_url())
            return response
        except serializers.ValidationError as e:
            # If username doesn't exist return with code 404,
            # else code 400
            non_field_errors = e.detail.get("non_field_errors", [])
            if "Unable to log in with provided credentials." in non_field_errors:
                username = request.data["username"]
                if not User.objects.filter(username__iexact=username).exists():
                    e.status_code = 404
            raise


class CreateOrAuthenticated(IsAuthenticatedOrReadOnly):
    def has_permission(self, request, view):
        if request.method in ["OPTIONS", "POST"]:
            return True

        return super().has_permission(request, view)


class PartOfGame(IsAuthenticatedOrReadOnly):
    def has_object_permission(self, request, view, game):
        if request.method == "POST":
            return request.user in game.players.all()

        return False


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = (CreateOrAuthenticated,)


def update_game(game, data):
    def update_field(key):
        if key in data:
            setattr(game, key, data[key])

    game_already_ended = game.has_ended

    if game.players.count() == 0:
        for i, p_id in enumerate(data["player_ids"]):
            GamePlayer.objects.create(
                game=game, user=User.objects.get(id=p_id), position=i
            )

    update_field("start_datetime")
    update_field("end_datetime")
    update_field("official")
    update_field("description")

    cards = game.ordered_cards()
    new_cards = data["cards"]

    last_card = cards.last()
    previous_cards = cards.count()
    if previous_cards > 0:
        last_card_data = new_cards[previous_cards - 1]
        chug_data = last_card_data.get("chug", {}).get("duration_in_milliseconds")
        if chug_data and not hasattr(last_card, "chug"):
            Chug.objects.create(card=last_card, duration_in_milliseconds=chug_data)

    for i, card_data in enumerate(new_cards[previous_cards:]):
        card = Card.objects.create(
            game=game,
            value=card_data["value"],
            suit=card_data["suit"],
            drawn_datetime=card_data["drawn_datetime"],
            index=previous_cards + i,
        )

        chug_data = card_data.get("chug", {}).get("duration_in_milliseconds")
        if chug_data:
            Chug.objects.create(card=card, duration_in_milliseconds=chug_data)

    game.save()

    if game.has_ended and not game_already_ended:
        PlayerStat.update_on_game_finished(game)


class OneResultSetPagination(PageNumberPagination):
    page_size = 1
    max_page_size = 1


class GameViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Game.objects.all()
    serializer_class = GameSerializer
    permission_classes = (CreateOrAuthenticated,)
    pagination_class = OneResultSetPagination

    def retrieve(self, request, pk=None):
        try:
            game = Game.objects.get(id=pk)
        except Game.DoesNotExist:
            raise serializers.ValidationError("Game doesn't exist")

        return Response(GameSerializerWithPlayerStats(game).data)

    def create(self, request):
        serializer = CreateGameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        game = serializer.save()

        players_str = ", ".join(map(lambda p: p.username, game.ordered_players()))
        game_url = request.build_absolute_uri("/games/{}/".format(game.id))

        post_to_page("A game between {} just started!".format(players_str), game_url)

        return Response(self.serializer_class(game).data)

    @action(detail=True, methods=["post"], permission_classes=[PartOfGame])
    def update_state(self, request, pk=None):
        try:
            game = Game.objects.get(id=pk)
        except Game.DoesNotExist:
            raise serializers.ValidationError("Game doesn't exist")

        self.check_object_permissions(request, game)

        serializer = GameSerializer(game, data=request.data)
        serializer.is_valid(raise_exception=True)

        update_game(game, serializer.validated_data)

        return Response({})


class RankedFacecardsView(viewsets.ViewSet):
    permission_classes = (IsAuthenticatedOrReadOnly,)

    def list(self, request):
        season = Season.current_season()

        facecards = {}
        for ranking, (suit, _) in zip(RANKINGS, Card.SUITS):
            qs = ranking.get_qs(season)

            for ps, value in zip(
                qs.exclude(user__image="")[: len(Card.FACE_CARD_VALUES)],
                Card.FACE_CARD_VALUES,
            ):
                user = ps.user
                facecards[f"{suit}-{value}"] = {
                    "user_id": user.id,
                    "user_username": user.username,
                    "user_image": user.image_url(),
                    "ranking_name": ranking.name,
                    "ranking_value": ranking.get_value(ps),
                }

        return Response(facecards)


class PlayerStatViewSet(viewsets.ViewSet):
    permission_classes = (IsAuthenticatedOrReadOnly,)

    def retrieve(self, request, pk=None):
        user = get_object_or_404(User, pk=pk)
        stats = PlayerStat.objects.filter(user=user)
        serializer = PlayerStatSerializer(stats, many=True)
        return Response(serializer.data)
