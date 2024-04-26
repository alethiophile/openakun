#!lua name=votes

-- This code is loaded into Redis as a function in order to carry out
-- vote operations atomically. Any operation that changes a value on a
-- vote generally must be done here, due to how votes are stored.

-- The scheme for vote storage is very simple: all information about a
-- given vote is stored in a Redis hash whose keys are vote ID
-- strings, and values are JSON text. This includes vote
-- configuration, options and votes.

-- for all these functions, "keys" will always be 1. the appropriate
-- "channel_votes:{channel_id}" key and 2. "vote_info"

local function Set(list)
   local set = {}
   for _, l in ipairs(list) do set[l] = true end
   return set
end

local function to_list(set)
   local list = {}
   for k, _ in pairs(set) do table.insert(list, k) end
   return list
end

-- call only after validating that the given vote is on the correct
-- channel
local function get_vote(id)
   local d = redis.call('HGET', 'vote_info', id)
   redis.log(redis.LOG_WARNING, d)
   local vote = cjson.decode(d)
   redis.log(redis.LOG_WARNING, cjson.encode(vote))
   for _, v in pairs(vote.votes) do
      v.users_voted_for = Set(v.users_voted_for)
   end
   return vote
end
redis.register_function('get_vote', get_vote)

local function set_vote(id, vote)
   -- local vote = cjson.decode(redis.call('HGET', 'vote_info', id))
   for _, v in pairs(vote.votes) do
      v.users_voted_for = to_list(v.users_voted_for)
   end
   redis.call('HSET', 'vote_info', id, cjson.encode(vote))
end
redis.register_function('set_vote', set_vote)

local function add_vote(keys, args)
   if not redis.call('SISMEMBER', keys[1], args[1]) == 1 then
      return false
   end
   local vote_id = args[1]
   local option_id = args[2]
   local user_id = args[3]
   local vote = get_vote(vote_id)
   if not vote.votes[option_id] then
      return false
   end
   if vote.votes[option_id].killed then
      return false
   end
   if not vote.multivote then
      for _, v in vote.votes do
         v.users_voted_for[user_id] = nil
      end
   end
   vote.votes[option_id].users_voted_for[user_id] = true
   set_vote(vote_id, vote)
   return true
end
redis.register_function('add_vote', add_vote)

local function remove_vote(keys, args)
   if not redis.call('SISMEMBER', keys[1], args[1]) == 1 then
      return false
   end
   local vote_id = args[1]
   local option_id = args[2]
   local user_id = args[3]
   local vote = get_vote(vote_id)
   if not vote.votes[option_id] then
      return false
   end
   vote.votes[option_id].users_voted_for[user_id] = nil
   set_vote(vote_id, vote)
   return true
end
redis.register_function('remove_vote', remove_vote)

local function new_vote_entry(keys, args)
   if not redis.call('SISMEMBER', keys[1], args[1]) == 1 then
      return false
   end
   local vote_id = args[1]
   local option_id = args[2]
   -- user_id may be nil here, in which case no new vote is set
   local user_id = args[3]
   local vote = get_vote(vote_id)
   if not vote.writein_allowed then
      return false
   end
   -- this should never happen but if it does it's an error
   if vote.votes[option_id] then
      return false
   end
   vote.votes[option_id] = { killed=false, users_voted_for={} }
   if user_id then
      vote.votes[option_id].users_voted_for[user_id] = true
   end
   set_vote(vote_id, vote)
   return true
end
redis.register_function('new_vote_entry', new_vote_entry)

local function set_option_killed(keys, args)
   if not redis.call('SISMEMBER', keys[1], args[1]) == 1 then
      return false
   end
   local vote_id = args[1]
   local option_id = args[2]
   local killed = args[3]
   local kill_string = args[4]
   local vote = get_vote(vote_id)
   if not vote.votes[option_id] then
      return false
   end
   if killed == '0' then
      vote.votes[option_id].killed = false
      vote.votes[option_id].killed_text = nil
   elseif kill_string == '' then
      vote.votes[option_id].killed = true
      vote.votes[option_id].killed_text = nil
   else
      vote.votes[option_id].killed = true
      vote.votes[option_id].killed_text = kill_string
   end
   set_vote(vote_id, vote)
   return true
end
redis.register_function('set_option_killed', set_option_killed)

local function set_vote_config(keys, args)
   if not redis.call('SISMEMBER', keys[1], args[1]) == 1 then
      return false
   end
   local vote_id = args[1]
   local vote_json = args[2]
   local vote_conf = cjson.decode(vote_json)
   local vote = get_vote(vote_id)
   if vote_conf.multivote then
      vote.multivote = true
   end
   if vote.writein_allowed ~= nil then
      vote.writein_allowed = vote_conf.writein_allowed
   end
   if vote.votes_hidden ~= nil then
      vote.votes_hidden = vote_conf.votes_hidden
   end
   if vote.close_time ~= nil then
      vote.close_time = vote_conf.close_time
   end
   set_vote(vote_id, vote)
   return true
end
redis.register_function('set_vote_config', set_vote_config)
