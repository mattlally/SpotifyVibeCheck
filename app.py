# app.py
import os
import time
from flask import Flask, request, redirect, session, url_for, render_template
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from collections import Counter, defaultdict
from datetime import datetime
import plotly.express as px
import pandas as pd
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Create an instance of the Flask class
app = Flask(__name__)

# Set the secret key for session management
app.secret_key = os.getenv('SECRET_KEY')

# Configure session storage
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False

# Spotify app credentials from environment variables
SPOTIPY_CLIENT_ID = os.getenv('SPOTIPY_CLIENT_ID')
SPOTIPY_CLIENT_SECRET = os.getenv('SPOTIPY_CLIENT_SECRET')
SPOTIPY_REDIRECT_URI = os.getenv('SPOTIPY_REDIRECT_URI')
SCOPE = os.getenv('SCOPE')

# Determine if the app is running in debug mode based on FLASK_ENV
DEBUG = os.getenv('FLASK_ENV') == 'development'

@app.route('/')
def home():
    return render_template('index.html')


@app.route('/login')
def login():
    # Spotify authorization endpoint
    auth_url = 'https://accounts.spotify.com/authorize'

    # Redirect to Spotify's auth page
    redirect_uri = f"{auth_url}?client_id={SPOTIPY_CLIENT_ID}&response_type=code&redirect_uri={SPOTIPY_REDIRECT_URI}&scope={SCOPE}"
    return redirect(redirect_uri)


@app.route('/callback')
def callback():
    session.clear()  # Clear session to remove any previous user's data
    session.modified = True  # Force session modification

    sp_oauth = SpotifyOAuth(client_id=SPOTIPY_CLIENT_ID, client_secret=SPOTIPY_CLIENT_SECRET,
                            redirect_uri=SPOTIPY_REDIRECT_URI, scope=SCOPE)

    code = request.args.get('code')
    token_info = sp_oauth.get_access_token(code)

    sp = spotipy.Spotify(auth=token_info['access_token'])
    user_profile = sp.current_user()
    user_id = user_profile['id']
    user_name = user_profile['display_name']

    # Log the fetched user ID and session contents for debugging
    print(f"User logged in: {user_name} (ID: {user_id})")

    # Store the token info and user ID in the session
    session["token_info"] = token_info
    session["user_id"] = user_id
    session["user_name"] = user_name  # Store the user's name in the session

    return redirect(url_for('loading'))


def get_token():
    token_info = session.get('token_info')
    if not token_info:
        flash("Session expired. Please log in again.", "warning")
        return redirect(url_for('login'))

    now = int(time.time())
    is_token_expired = token_info['expires_at'] - now < 60

    if is_token_expired:
        sp_oauth = SpotifyOAuth(client_id=SPOTIPY_CLIENT_ID, client_secret=SPOTIPY_CLIENT_SECRET,
                                redirect_uri=SPOTIPY_REDIRECT_URI, scope=SCOPE)
        token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
        session['token_info'] = token_info

    return token_info


@app.route('/loading')
def loading():
    return render_template('loading.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


@app.route('/get_recently_played')
def get_recently_played():
    if 'user_id' not in session:
        flash("Session expired. Please log in again.", "warning")
        return redirect(url_for('login'))

    token_info = get_token()
    sp = spotipy.Spotify(auth=token_info['access_token'])
    user_profile = sp.current_user()
    user_id = user_profile['id']
    user_name = user_profile['display_name']
    user_image = user_profile['images'][0]['url'] if user_profile['images'] else 'default_profile_image_url'

    # Log the user information retrieved from the Spotify API
    print(f"User loaded from Spotify API: {user_name} (ID: {user_id})")

    # Log the user information stored in the session
    session_user_name = session.get('user_name')
    session_user_id = session.get('user_id')
    print(f"User stored in session: {session_user_name} (ID: {session_user_id})")

    # Force logout if session user ID does not match the Spotify API user ID
    if session.get('user_id') != user_id:
        flash("Session mismatch detected. Please log in again.", "error")
        return redirect(url_for('logout'))

    # Fetch the user's recently played tracks
    results = sp.current_user_recently_played(limit=50)

    # Initialize an empty list to store the track information
    tracks = []
    track_names = []
    track_ids = []
    artists = []
    album_covers = []
    genres = defaultdict(int)
    genre_track_map = defaultdict(list)
    # Iterate over the items in the results
    for item in results['items']:
        # Get the track information
        track = item['track']
        track_name = f"{track['name']} by {track['artists'][0]['name']}"
        track_names.append(track_name)
        track_ids.append(track['id'])
        artists.append(track['artists'][0]['id'])
        album_covers.append(track['album']['images'][0]['url'])
        # Get the time the track was played at
        played_at = item['played_at']
        # Convert played_at to a datetime object and format it
        try:
            played_at_dt = datetime.strptime(played_at, '%Y-%m-%dT%H:%M:%S.%fZ')
        except ValueError:
            played_at_dt = datetime.strptime(played_at, '%Y-%m-%dT%H:%M:%SZ')
        played_at_formatted = played_at_dt.strftime('%Y-%m-%d %H:%M:%S')
        # Append the track information to the list
        tracks.append(f"{track_name} at {played_at_formatted}")

    # Get the audio features for the tracks
    audio_features = get_audio_features(sp, track_ids)
    # Extract valence and energy for each track
    valences = [feat['valence'] for feat in audio_features]
    energies = [feat['energy'] for feat in audio_features]

    # Combine track names, valences, energies, and played times
    track_info = [f"{name} - Valence: {valence}, Energy: {energy} at {played_at}"
                  for name, valence, energy, played_at in zip(track_names, valences, energies, tracks)]

    # Calculate the top 3 most listened-to songs
    track_counts = Counter(track_names)
    top_tracks = track_counts.most_common(3)
    if len(top_tracks) > 0:
        top_tracks_names, top_tracks_counts = zip(*top_tracks)
        top_song = top_tracks_names[0]
    else:
        top_song = "No data"

    # Get genres for the artists
    artist_info_cache = {}
    for artist_id, track_name in zip(artists, track_names):
        if artist_id not in artist_info_cache:
            artist_info = sp.artist(artist_id)
            artist_info_cache[artist_id] = artist_info
        else:
            artist_info = artist_info_cache[artist_id]
        for genre in artist_info['genres']:
            genres[genre] += 1  # Increment by 1 for each listen
            genre_track_map[genre].append(f"{track_name} - 1 play")

    most_common_genres = Counter(genres).most_common(3)  # Get top 3 genres
    if len(most_common_genres) > 0:
        top_genres = [genre for genre, count in most_common_genres]
    else:
        top_genres = ["No data"]

    # Determine the top artist and fetch their image
    artist_counts = Counter(artists)
    if len(artist_counts) > 0:
        top_artist_id, _ = artist_counts.most_common(1)[0]
        top_artist_info = artist_info_cache[top_artist_id]
        top_artist = top_artist_info['name']
        top_artist_image = top_artist_info['images'][0]['url'] if top_artist_info['images'] else ''
    else:
        top_artist = "No data"
        top_artist_image = ""

    # Determine the top album cover
    if top_song != "No data":
        top_album_cover = album_covers[track_names.index(top_song)]
    else:
        top_album_cover = ""

    # Create a DataFrame for the scatter plots
    df = pd.DataFrame({
        'track_name': track_names,
        'valence': valences,
        'energy': energies,
    })

    # Generate the scatter plot and get the quadrant with the most songs
    scatter_plot_data_valence_energy, max_quad, quad_counts, quadrant_labels = generate_scatter_plot(df, 'valence', 'energy', '')

    user_vibe = quadrant_labels[max_quad]  # Corrected user vibe

    # Map user vibe to corresponding color
    vibe_colors = {
        "melancholic": "#bcb8b1",
        "fiery": "#d90429",
        "chill": "#168aad",
        "euphoric": "#ffd60a"
    }
    vibe_color = vibe_colors.get(user_vibe, "#1DB954")  # Default to Spotify green if not found

    # Define emojis for each vibe
    vibe_emojis = {
        "melancholic": "😢",
        "fiery": "🔥",
        "chill": "🥶",
        "euphoric": "😄"
    }
    user_vibe_emoji = vibe_emojis.get(user_vibe, "")

    # Calculate top genres
    top_genres_list = [f"{top_genres[0].split('(')[0].strip()}" if len(top_genres) > 0 else "",
                       f"{top_genres[1].split('(')[0].strip()}" if len(top_genres) > 1 else "",
                       f"{top_genres[2].split('(')[0].strip()}" if len(top_genres) > 2 else ""]

    # Ensure `top_genres_list` is passed to the template
    return render_template('recently_played.html', scatter_plot_data_valence_energy=scatter_plot_data_valence_energy,
                           user_vibe=user_vibe.lower(), user_vibe_emoji=user_vibe_emoji, vibe_color=vibe_color,
                           top_song=top_song, top_artist=top_artist, top_genres=top_genres_list,
                           top_album_cover=top_album_cover, top_artist_image=top_artist_image, quad_counts=quad_counts,
                           user_name=user_name, user_image=user_image)

def get_audio_features(sp, track_ids):
    audio_features = sp.audio_features(track_ids)
    return audio_features

def generate_scatter_plot(df, x_col, y_col, title):
    # Count occurrences for proportional sizing
    df['count'] = df.groupby(['track_name'])[x_col].transform('count')

    # Custom hover text
    hover_text = df.apply(lambda row: f"<b>Song:</b> {row['track_name']}<br><b>Valence:</b> {row[x_col]:.2f}<br><b>Energy:</b> {row[y_col]:.2f}<br><b>Listens:</b> {int(row['count'])}", axis=1)

    fig = px.scatter(
        df,
        x=x_col,
        y=y_col,
        size='count',
        hover_name='track_name',
        title='',
        labels={x_col: "Valence", y_col: "Energy"},
        color_discrete_sequence=['#1DB954'],  # Spotify green color
        custom_data=['track_name', x_col, y_col, 'count']
    )

    fig.update_traces(
        marker=dict(line=dict(color='white', width=1)),  # White border for dots
        hovertemplate='<b>Song:</b> %{customdata[0]}<br><b>Valence:</b> %{customdata[1]:.2f}<br><b>Energy:</b> %{customdata[2]:.2f}<br><b>Listens:</b> %{customdata[3]:d}'
    )

    # Adding dotted lines at x=0.5 and y=0.5
    fig.add_shape(type="line", x0=0.5, x1=0.5, y0=0, y1=1, line=dict(color="gray", dash="dot"))
    fig.add_shape(type="line", x0=0, x1=1, y0=0.5, y1=0.5, line=dict(color="gray", dash="dot"))

    # Determine which quadrant has the most songs
    quad_counts = [
        ((df[x_col] > 0.5) & (df[y_col] > 0.5)).sum(),  # Top right
        ((df[x_col] <= 0.5) & (df[y_col] > 0.5)).sum(),  # Top left
        ((df[x_col] <= 0.5) & (df[y_col] <= 0.5)).sum(),  # Bottom left
        ((df[x_col] > 0.5) & (df[y_col] <= 0.5)).sum()   # Bottom right
    ]
    max_quad = quad_counts.index(max(quad_counts))

    # Define quadrant labels
    quadrant_labels = ['euphoric', 'fiery', 'melancholic', 'chill']  # Ensure this is defined

    # Define color mapping for each quadrant
    vibe_colors = {
        "euphoric": "#ffd60a",
        "fiery": "#d90429",
        "melancholic": "#bcb8b1",
        "chill": "#168aad"
    }
    shade_color = vibe_colors[quadrant_labels[max_quad]]

    # Adding shaded quadrant
    if max_quad == 0:
        fig.add_shape(type="rect", x0=0.5, x1=1, y0=0.5, y1=1, fillcolor=f'rgba({int(shade_color[1:3], 16)}, {int(shade_color[3:5], 16)}, {int(shade_color[5:7], 16)}, 0.3)', line=dict(color="gray", width=1))
    elif max_quad == 1:
        fig.add_shape(type="rect", x0=0, x1=0.5, y0=0.5, y1=1, fillcolor=f'rgba({int(shade_color[1:3], 16)}, {int(shade_color[3:5], 16)}, {int(shade_color[5:7], 16)}, 0.3)', line=dict(color="gray", width=1))
    elif max_quad == 2:
        fig.add_shape(type="rect", x0=0, x1=0.5, y0=0, y1=0.5, fillcolor=f'rgba({int(shade_color[1:3], 16)}, {int(shade_color[3:5], 16)}, {int(shade_color[5:7], 16)}, 0.3)', line=dict(color="gray", width=1))
    else:
        fig.add_shape(type="rect", x0=0.5, x1=1, y0=0, y1=0.5, fillcolor=f'rgba({int(shade_color[1:3], 16)}, {int(shade_color[3:5], 16)}, {int(shade_color[5:7], 16)}, 0.3)', line=dict(color="gray", width=1))

    fig.update_layout(
        xaxis=dict(
            range=[0, 1],
            tickmode='array',
            tickvals=[0, 0.5, 1],
            ticktext=['0<br>😢', '0.5<br>😐', '1<br>😁'],
            title=dict(
                text='Valence (Musical Positiveness)',
                font=dict(size=13),  # Adjust the font size if needed
            ),
            showticklabels=True,
            ticks='outside',
            showline=True,  # Show axis line
            linecolor='white',  # Axis line color
            linewidth=2,  # Axis line width
            mirror=True,
            tickwidth=2,  # Width of the tick marks
            ticklen=10,
            tickfont=dict(size=13)  # Increase the font size of the tick labels
        ),
        yaxis=dict(
            range=[0, 1],
            tickmode='array',
            tickvals=[0, 0.5, 1],
            ticktext=['0<br>🥱', '0.5<br>😐', '1<br>🤘'],
            title=dict(
                text='<br>Energy (Musical Intensity)',
                font=dict(size=13),  # Adjust the font size if needed
            ),
            showticklabels=True,
            ticks='outside',
            showline=True,  # Show axis line
            linecolor='white',  # Axis line color
            linewidth=2,  # Axis line width
            mirror=True,
            tickwidth=2,  # Width of the tick marks
            ticklen=10,
            tickfont=dict(size=13)  # Increase the font size of the tick labels
        ),
        autosize=True,
        plot_bgcolor='black',  # Black background
        paper_bgcolor='black',  # Set the paper background color to black
        margin=dict(l=50, r=50, t=50, b=50),  # Set margins to create space for the border
        font=dict(color='white')  # Set the font color to white
    )

    fig.update_layout(
        margin=dict(t=10),  # Adjust the top margin if needed
        yaxis_automargin=True,  # Enable automargin for y-axis
        xaxis_automargin=True
    )

    # Adding descriptive words for each quadrant
    fig.add_annotation(x=0.25, y=0.25, text="Melancholic", showarrow=False, font=dict(color="white", size=12))
    fig.add_annotation(x=0.25, y=0.75, text="Fiery", showarrow=False, font=dict(color="white", size=12))
    fig.add_annotation(x=0.75, y=0.25, text="Chill", showarrow=False, font=dict(color="white", size=12))
    fig.add_annotation(x=0.75, y=0.75, text="Euphoric", showarrow=False, font=dict(color="white", size=12))

    # Calculate sizeref as the maximum size of the dots divided by the maximum value of 'count'
    desired_max_size = 15
    sizeref = 2. * max(df['count']) / (desired_max_size ** 2)

    # Update the scatter plot trace with the calculated sizeref
    fig.update_traces(
        marker=dict(
            size=df['count'],
            sizemode='area',
            sizeref=sizeref,
            line=dict(color='white', width=1)
        ),
        hovertemplate='<b>Song:</b> %{customdata[0]}<br><b>Valence:</b> %{customdata[1]:.2f}<br><b>Energy:</b> %{customdata[2]:.2f}<br><b>Listens:</b> %{customdata[3]:d}'
    )

    # Ensure toolbar is disabled
    config = {'displayModeBar': False}
    return fig.to_html(full_html=False, config=config), max_quad, quad_counts, quadrant_labels


# Run the Flask app
if __name__ == '__main__':
    app.run(debug=DEBUG, port=5001)  # Run the app on port 5001
