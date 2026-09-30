import datetime
import calendar
import requests
import os
from lxml import etree
import time
import hashlib
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Fine-grained personal access token with All Repositories access:
# Account permissions: read:Followers, read:Starring, read:Watching
# Repository permissions: read:Commit statuses, read:Contents, read:Issues, read:Metadata, read:Pull Requests
# Issues and pull requests permissions not needed at the moment, but may be used in the future
ACCESS_TOKEN = os.environ.get('ACCESS_TOKEN') or os.environ.get('GITHUB_TOKEN', '')
HEADERS = {'Authorization': f'Bearer {ACCESS_TOKEN}'} if ACCESS_TOKEN else {}
USER_NAME = os.environ.get('USER_NAME') or os.environ.get('GITHUB_REPOSITORY_OWNER') or 'VivekChittibothula'
TIME_ZONE = os.environ.get('TIME_ZONE', 'Asia/Kolkata')
INACTIVE_DAYS = 30
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0, 'recursive_loc': 0, 'graph_commits': 0, 'loc_query': 0}


def simple_request(func_name, query, variables):
    """
    Returns a request, or raises an Exception if the response does not succeed.
    """
    if not ACCESS_TOKEN:
        raise RuntimeError(
            'No GitHub token is configured. Add ACCESS_TOKEN as a repository secret '
            'or run the workflow with its GITHUB_TOKEN.'
        )
    request = requests.post(
        'https://api.github.com/graphql',
        json={'query': query, 'variables': variables},
        headers=HEADERS,
        timeout=60,
    )
    try:
        payload = request.json()
    except ValueError:
        payload = None
    if request.status_code != 200:
        raise RuntimeError(
            f'{func_name} failed with HTTP {request.status_code}: {request.text}'
        )
    if payload and payload.get('errors'):
        messages = '; '.join(error.get('message', str(error)) for error in payload['errors'])
        raise RuntimeError(f'{func_name} failed in GitHub GraphQL: {messages}')
    return request


def graph_commits(start_date, end_date):
    """
    Uses GitHub's GraphQL v4 API to return my total commit count
    """
    query_count('graph_commits')
    query = '''
    query($start_date: DateTime!, $end_date: DateTime!, $login: String!) {
        user(login: $login) {
            contributionsCollection(from: $start_date, to: $end_date) {
                contributionCalendar {
                    totalContributions
                }
            }
        }
    }'''
    variables = {'start_date': start_date,'end_date': end_date, 'login': USER_NAME}
    request = simple_request(graph_commits.__name__, query, variables)
    return int(request.json()['data']['user']['contributionsCollection']['contributionCalendar']['totalContributions'])


def graph_repos_stars(count_type, owner_affiliation, cursor=None, add_loc=0, del_loc=0):
    """
    Uses GitHub's GraphQL v4 API to return my total repository, star, or lines of code count.
    """
    query_count('graph_repos_stars')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers {
                                totalCount
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    if count_type not in {'repos', 'stars'}:
        raise ValueError(f'Unsupported repository count type: {count_type}')

    total_stars = 0
    total_repositories = 0
    while True:
        variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
        request = simple_request(graph_repos_stars.__name__, query, variables)
        repositories = request.json()['data']['user']['repositories']
        total_repositories = repositories['totalCount']
        total_stars += stars_counter(repositories['edges'])
        if not repositories['pageInfo']['hasNextPage']:
            return total_repositories if count_type == 'repos' else total_stars
        cursor = repositories['pageInfo']['endCursor']


def recursive_loc(owner, repo_name, data, cache_comment, addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    """
    Uses GitHub's GraphQL v4 API and cursor pagination to fetch 100 commits from a repository at a time
    """
    query_count('recursive_loc')
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit {
                                        committedDate
                                    }
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    deletions
                                    additions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }'''
    variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
    request = requests.post(
        'https://api.github.com/graphql',
        json={'query': query, 'variables': variables},
        headers=HEADERS,
        timeout=60,
    ) # I cannot use simple_request(), because I want to save the file before raising Exception
    if request.status_code == 200:
        payload = request.json()
        if payload.get('errors'):
            force_close_file(data, cache_comment)
            messages = '; '.join(error.get('message', str(error)) for error in payload['errors'])
            raise RuntimeError(f'recursive_loc failed in GitHub GraphQL: {messages}')
        if payload['data']['repository']['defaultBranchRef'] != None: # Only count commits if repo isn't empty
            return loc_counter_one_repo(owner, repo_name, data, cache_comment, payload['data']['repository']['defaultBranchRef']['target']['history'], addition_total, deletion_total, my_commits)
        else: return 0
    force_close_file(data, cache_comment) # saves what is currently in the file before this program crashes
    if request.status_code == 403:
        raise Exception('Too many requests in a short amount of time!\nYou\'ve hit the non-documented anti-abuse limit!')
    raise Exception('recursive_loc() has failed with a', request.status_code, request.text, QUERY_COUNT)


def loc_counter_one_repo(owner, repo_name, data, cache_comment, history, addition_total, deletion_total, my_commits):
    """
    Recursively call recursive_loc (since GraphQL can only search 100 commits at a time) 
    only adds the LOC value of commits authored by me
    """
    for node in history['edges']:
        if node['node']['author']['user'] == OWNER_ID:
            my_commits += 1
            addition_total += node['node']['additions']
            deletion_total += node['node']['deletions']

    if history['edges'] == [] or not history['pageInfo']['hasNextPage']:
        return addition_total, deletion_total, my_commits
    else: return recursive_loc(owner, repo_name, data, cache_comment, addition_total, deletion_total, my_commits, history['pageInfo']['endCursor'])


def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=None):
    """
    Uses GitHub's GraphQL v4 API to query all the repositories I have access to (with respect to owner_affiliation)
    Queries 60 repos at a time, because larger queries give a 502 timeout error and smaller queries send too many
    requests and also give a 502 error.
    Returns the total number of lines of code in all repositories
    """
    query_count('loc_query')
    if edges is None:
        edges = []
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
            edges {
                node {
                    ... on Repository {
                        nameWithOwner
                        defaultBranchRef {
                            target {
                                ... on Commit {
                                    history(first: 1) {
                                        totalCount
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(loc_query.__name__, query, variables)
    if request.json()['data']['user']['repositories']['pageInfo']['hasNextPage']:   # If repository data has another page
        edges += request.json()['data']['user']['repositories']['edges']            # Add on to the LoC count
        return loc_query(owner_affiliation, comment_size, force_cache, request.json()['data']['user']['repositories']['pageInfo']['endCursor'], edges)
    else:
        return cache_builder(edges + request.json()['data']['user']['repositories']['edges'], comment_size, force_cache)


def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    """
    Checks each repository in edges to see if it has been updated since the last time it was cached
    If it has, run recursive_loc on that repository to update the LOC count
    """
    cached = True # Assume all repositories are cached
    filename = 'cache/'+hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()+'.txt' # Create a unique filename for each user
    try:
        with open(filename, 'r') as f:
            data = f.readlines()
    except FileNotFoundError: # If the cache file doesn't exist, create it
        data = []
        if comment_size > 0:
            for _ in range(comment_size): data.append('This line is a comment block. Write whatever you want here.\n')
        with open(filename, 'w') as f:
            f.writelines(data)

    expected_hashes = [
        hashlib.sha256(edge['node']['nameWithOwner'].encode('utf-8')).hexdigest()
        for edge in edges
    ]
    cached_entries = data[comment_size:]
    cache_matches_repositories = (
        len(cached_entries) == len(expected_hashes)
        and all(entry.split()[:1] == [repo_hash] for entry, repo_hash in zip(cached_entries, expected_hashes))
    )
    if not cache_matches_repositories or force_cache: # If the repositories or their order changed, rebuild the cache
        cached = False
        flush_cache(edges, filename, comment_size)
        with open(filename, 'r') as f:
            data = f.readlines()

    cache_comment = data[:comment_size] # save the comment block
    data = data[comment_size:] # remove those lines
    for index, edge in enumerate(edges):
        node = edge['node']
        repo_hash, commit_count, *__ = data[index].split()
        history = None
        if node.get('defaultBranchRef') and node['defaultBranchRef'].get('target'):
            history = node['defaultBranchRef']['target'].get('history')
        commit_total = history['totalCount'] if history else 0
        if int(commit_count) != commit_total:
            if history is None: # The repository is empty.
                data[index] = repo_hash + ' 0 0 0 0\n'
            else:
                # If the commit count changed, update LOC for that repository.
                owner, repo_name = node['nameWithOwner'].split('/')
                loc = recursive_loc(owner, repo_name, data, cache_comment)
                data[index] = repo_hash + ' ' + str(commit_total) + ' ' + str(loc[2]) + ' ' + str(loc[0]) + ' ' + str(loc[1]) + '\n'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    for line in data:
        loc = line.split()
        loc_add += int(loc[3])
        loc_del += int(loc[4])
    return [loc_add, loc_del, loc_add - loc_del, cached]


def flush_cache(edges, filename, comment_size):
    """
    Wipes the cache file
    This is called when the number of repositories changes or when the file is first created
    """
    with open(filename, 'r') as f:
        data = []
        if comment_size > 0:
            data = f.readlines()[:comment_size] # only save the comment
    with open(filename, 'w') as f:
        f.writelines(data)
        for node in edges:
            f.write(hashlib.sha256(node['node']['nameWithOwner'].encode('utf-8')).hexdigest() + ' 0 0 0 0\n')


def force_close_file(data, cache_comment):
    """
    Forces the file to close, preserving whatever data was written to it
    This is needed because if this function is called, the program would've crashed before the file is properly saved and closed
    """
    filename = 'cache/'+hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()+'.txt'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    print('There was an error while writing to the cache file. The file,', filename, 'has had the partial data saved and closed.')


def stars_counter(data):
    """
    Count total stars in repositories owned by me
    """
    total_stars = 0
    for node in data: total_stars += node['node']['stargazers']['totalCount']
    return total_stars


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data, greeting_data, activity_data):
    """
    Update the dynamic profile values in the SVG and save the file.
    """
    tree = etree.parse(filename)
    root = tree.getroot()
    values = {
        'greeting_data': greeting_data,
        'activity_data': activity_data,
        'age_data': age_data,
        'commit_data': commit_data,
        'star_data': star_data,
        'repo_data': repo_data,
        'contrib_data': contrib_data,
        'follower_data': follower_data,
        'loc_data': loc_data[2],
        'loc_add': loc_data[0],
        'loc_del': loc_data[1],
    }
    for element_id, value in values.items():
        find_and_replace(root, element_id, format_value(value))
    tree.write(filename, encoding='UTF-8', xml_declaration=True, pretty_print=True)


def format_value(value):
    """Return a human-readable value without changing already formatted strings."""
    if isinstance(value, str):
        return value
    return f'{value:,}'


def find_and_replace(root, element_id, new_text):
    """
    Finds the element in the SVG file and replaces its text with a new value
    """
    elements = root.xpath(f"//*[@id='{element_id}']")
    if elements:
        elements[0].text = str(new_text)


def birth_date_from_environment():
    """Read the private birth date used only to calculate the displayed age."""
    raw_birth_date = os.environ.get('BIRTH_DATE', '').strip()
    if not raw_birth_date:
        raise RuntimeError(
            'BIRTH_DATE is required. Add it as a GitHub Actions secret in YYYY-MM-DD format.'
        )
    try:
        return datetime.date.fromisoformat(raw_birth_date)
    except ValueError as error:
        raise RuntimeError('BIRTH_DATE must use YYYY-MM-DD format.') from error


def profile_today():
    """Return today's date in the profile owner's configured time zone."""
    try:
        return datetime.datetime.now(ZoneInfo(TIME_ZONE)).date()
    except ZoneInfoNotFoundError as error:
        raise RuntimeError(f'TIME_ZONE is not a known IANA time zone: {TIME_ZONE}') from error


def format_greeting(now=None):
    """Return a time-zone-aware greeting for the profile card."""
    if now is None:
        try:
            now = datetime.datetime.now(ZoneInfo(TIME_ZONE))
        except ZoneInfoNotFoundError as error:
            raise RuntimeError(f'TIME_ZONE is not a known IANA time zone: {TIME_ZONE}') from error
    if now.hour < 12:
        period = 'morning'
    elif now.hour < 18:
        period = 'afternoon'
    else:
        period = 'evening'
    return f'Good {period}, visitor!'


def calculate_age(birth_date, today=None):
    """Calculate calendar age as years, months, and days."""
    today = profile_today() if today is None else today
    if birth_date > today:
        raise ValueError('BIRTH_DATE cannot be in the future.')
    years = today.year - birth_date.year
    anniversary = safe_date(today.year, birth_date.month, birth_date.day)
    if anniversary > today:
        years -= 1
        anniversary = safe_date(today.year - 1, birth_date.month, birth_date.day)

    months = (today.year - anniversary.year) * 12 + today.month - anniversary.month
    month_anchor = add_months(anniversary, months)
    if month_anchor > today:
        months -= 1
        month_anchor = add_months(anniversary, months)

    days = (today - month_anchor).days
    return years, months, days


def safe_date(year, month, day):
    """Create a date while handling birthdays such as 29 February."""
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, min(day, last_day))


def add_months(value, months):
    """Add calendar months while keeping the day within the target month."""
    month_index = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(month_index, 12)
    return safe_date(year, month_index + 1, value.day)


def format_age(age):
    """Format a calendar age for the profile card."""
    years, months, days = age
    return f'{years} years, {months} months, {days} days'


def commit_counter(comment_size):
    """
    Counts up my total commits, using the cache file created by cache_builder.
    """
    total_commits = 0
    filename = 'cache/'+hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()+'.txt' # Use the same filename as cache_builder
    with open(filename, 'r') as f:
        data = f.readlines()
    cache_comment = data[:comment_size] # save the comment block
    data = data[comment_size:] # remove those lines
    for line in data:
        total_commits += int(line.split()[2])
    return total_commits


def cached_loc_data(comment_size):
    """Read the last known LOC totals when a live LOC refresh is unavailable."""
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    try:
        with open(filename, 'r') as f:
            data = f.readlines()[comment_size:]
    except FileNotFoundError:
        return [0, 0, 0, True]

    loc_add = 0
    loc_del = 0
    for line in data:
        fields = line.split()
        if len(fields) >= 5:
            loc_add += int(fields[3])
            loc_del += int(fields[4])
    return [loc_add, loc_del, loc_add - loc_del, True]


def user_getter(username):
    """
    Returns the account ID and creation time of the user
    """
    query_count('user_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            id
            createdAt
        }
    }'''
    variables = {'login': username}
    request = simple_request(user_getter.__name__, query, variables)
    return {'id': request.json()['data']['user']['id']}, request.json()['data']['user']['createdAt']

def follower_getter(username):
    """
    Returns the number of followers of the user
    """
    query_count('follower_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }'''
    request = simple_request(follower_getter.__name__, query, {'login': username})
    return int(request.json()['data']['user']['followers']['totalCount'])


def activity_message(repository, now=None):
    """Format recent activity, or show a message after a period of inactivity."""
    if not repository or not repository.get('pushed_at'):
        return "Sorry, I've been busy lately."
    now = now or datetime.datetime.now(ZoneInfo(TIME_ZONE))
    pushed_date = datetime.datetime.fromisoformat(
        repository['pushed_at'].replace('Z', '+00:00')
    ).astimezone(ZoneInfo(TIME_ZONE))
    days_since_activity = (now.date() - pushed_date.date()).days
    if days_since_activity > INACTIVE_DAYS:
        return "Sorry, I've been busy lately."
    return f"{repository.get('name', 'Unknown repository')} · {pushed_date.strftime('%d %b %Y')}"


def recent_activity(username=None):
    """Return the latest owned repository, or the inactive status message."""
    username = username or USER_NAME
    url = f'https://api.github.com/users/{quote(username, safe="")}/repos'
    response = requests.get(
        url,
        params={'type': 'owner', 'sort': 'pushed', 'direction': 'desc', 'per_page': 1},
        headers=HEADERS,
        timeout=60,
    )
    if response.status_code != 200:
        print(f'Warning: recent activity request failed with HTTP {response.status_code}')
        return 'Activity unavailable'
    repositories = response.json()
    return activity_message(repositories[0] if repositories else None)


def query_count(funct_id):
    """
    Counts how many times the GitHub GraphQL API is called
    """
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, *args):
    """
    Calculates the time it takes for a function to run
    Returns the function result and the time differential
    """
    start = time.perf_counter()
    funct_return = funct(*args)
    return funct_return, time.perf_counter() - start


def formatter(query_type, difference, funct_return=False, whitespace=0):
    """
    Prints a formatted time differential
    Returns formatted result if whitespace is specified, otherwise returns raw result
    """
    print('{:<23}'.format('   ' + query_type + ':'), sep='', end='')
    print('{:>12}'.format('%.4f' % difference + ' s ')) if difference > 1 else print('{:>12}'.format('%.4f' % (difference * 1000) + ' ms'))
    if whitespace:
        return f"{'{:,}'.format(funct_return): <{whitespace}}"
    return funct_return


if __name__ == '__main__':
    """
    Vivek Chittibothula — automatic profile statistics updater.
    """
    print('Calculation times:')

    greeting_data = format_greeting()
    age_data = format_age(calculate_age(birth_date_from_environment()))

    # Identify the account and calculate live GitHub statistics.
    user_data, user_time = perf_counter(user_getter, USER_NAME)
    OWNER_ID, acc_date = user_data
    formatter('account data', user_time)

    # Fetch the small, important profile statistics before the more expensive LOC scan.
    star_data, star_time = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    repo_data, repo_time = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    contrib_data, contrib_time = perf_counter(
        graph_repos_stars,
        'repos',
        ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER']
    )
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)
    activity_data, activity_time = perf_counter(recent_activity)
    formatter('recent activity', activity_time)

    # Cache and calculate commits + lines of code from repositories I can access.
    # A transient LOC/API failure should not prevent age and repository totals from updating.
    loc_time = 0
    commit_time = 0
    try:
        total_loc, loc_time = perf_counter(
            loc_query,
            ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'],
            7
        )
        formatter('LOC (cached)', loc_time) if total_loc[-1] else formatter('LOC (no cache)', loc_time)
        commit_data, commit_time = perf_counter(commit_counter, 7)
    except Exception as error:
        print(f'Warning: LOC/commit refresh skipped: {error}')
        total_loc = cached_loc_data(7)
        commit_data = 0
        try:
            commit_data, commit_time = perf_counter(commit_counter, 7)
        except FileNotFoundError:
            pass

    # Format added/deleted/total LOC for display.
    for index in range(len(total_loc) - 1):
        total_loc[index] = '{:,}'.format(total_loc[index])

    svg_overwrite(
        'dark_mode.svg',
        age_data,
        commit_data,
        star_data,
        repo_data,
        contrib_data,
        follower_data,
        total_loc[:-1],
        greeting_data,
        activity_data
    )
    svg_overwrite(
        'light_mode.svg',
        age_data,
        commit_data,
        star_data,
        repo_data,
        contrib_data,
        follower_data,
        total_loc[:-1],
        greeting_data,
        activity_data
    )

    total_time = user_time + loc_time + commit_time + star_time + repo_time + contrib_time + follower_time + activity_time
    print('Total function time:', '{:.4f}'.format(total_time), 's')
    print('Total GitHub GraphQL API calls:', sum(QUERY_COUNT.values()))
    for funct_name, count in QUERY_COUNT.items():
        print('{:<28}'.format('   ' + funct_name + ':'), '{:>6}'.format(count))
