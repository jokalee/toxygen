import util.util as util
import util.ui as util_ui
from contacts.friend import Friend
import os
from PyQt5 import QtCore, QtGui, QtWidgets
from messenger.messages import *
from wrapper.toxcore_enums_and_consts import *
from network.tox_dns import tox_dns


class ContactsManager:

    def __init__(self, tox, settings, screen, profile_manager):
        self._tox = tox
        self._settings = settings
        self._screen = screen
        self._profile_manager = profile_manager
        self._messages = screen.messages
        self._contacts, self._active_friend = [], -1
        self._sorting = settings['sorting']
        self._filter_string = ''
        self._friend_item_height = 40 if settings['compact_mode'] else 70
        screen.online_contacts.setCurrentIndex(int(self._sorting))
        self.load_contacts()

    def load_contacts(self):
        self.load_friends()
        self.load_groups()
        if len(self._contacts):
            self.set_active(0)
        self.filtration_and_sorting(self._sorting)

    def load_friends(self):
        aliases = self._settings['friends_aliases']
        friend_numbers = self._tox.self_get_friend_list()
        for friend_number in friend_numbers:  # creates list of friends
            tox_id = self._tox.friend_get_public_key(friend_number)
            try:
                alias = list(filter(lambda x: x[0] == tox_id, aliases))[0][1]
            except:
                alias = ''
            item = self.create_friend_item()
            name = alias or self._tox.friend_get_name(friend_number) or tox_id
            status_message = self._tox.friend_get_status_message(i)
            if not self._history.friend_exists_in_db(tox_id):
                self._history.add_friend_to_db(tox_id)
            message_getter = self._history.messages_getter(tox_id)
            friend = Friend(self._profile_manager, message_getter, friend_number, name, status_message, item, tox_id)
            friend.set_alias(alias)
            self._contacts.append(friend)

    def load_groups(self):
        pass

    def get_friend(self, num):
        if num < 0 or num >= len(self._contacts):
            return None
        return self._contacts[num]

    def get_curr_friend(self):
        return self._contacts[self._active_friend] if self._active_friend + 1 else None

    def save_profile(self):
        data = self._tox.get_savedata()
        self._profile_manager.save_profile(data)

    # -----------------------------------------------------------------------------------------------------------------
    # Work with active friend
    # -----------------------------------------------------------------------------------------------------------------

    def get_active(self):
        return self._active_friend

    def set_active(self, value=None):
        """
        Change current active friend or update info
        :param value: number of new active friend in friend's list or None to update active user's data
        """
        if value is None and self._active_friend == -1:  # nothing to update
            return
        if value == -1:  # all friends were deleted
            self._screen.account_name.setText('')
            self._screen.account_status.setText('')
            self._screen.account_status.setToolTip('')
            self._active_friend = -1
            self._screen.account_avatar.setHidden(True)
            self._messages.clear()
            self._screen.messageEdit.clear()
            return
        try:
            self.send_typing(False)
            self._screen.typing.setVisible(False)
            if value is not None:
                if self._active_friend + 1 and self._active_friend != value:
                    try:
                        self.get_curr_friend().curr_text = self._screen.messageEdit.toPlainText()
                    except:
                        pass
                friend = self._contacts[value]
                friend.remove_invalid_unsent_files()
                if self._active_friend != value:
                    self._screen.messageEdit.setPlainText(friend.curr_text)
                self._active_friend = value
                friend.reset_messages()
                if not self._settings['save_history']:
                    friend.delete_old_messages()
                self._messages.clear()
                friend.load_corr()
                messages = friend.get_corr()[-PAGE_SIZE:]
                self._load_history = False
                for message in messages:
                    if message.get_type() <= 1:
                        data = message.get_data()
                        self.create_message_item(data[0],
                                                 data[2],
                                                 data[1],
                                                 data[3])
                    elif message.get_type() == MESSAGE_TYPE['FILE_TRANSFER']:
                        if message.get_status() is None:
                            self.create_unsent_file_item(message)
                            continue
                        item = self.create_file_transfer_item(message)
                        if message.get_status() in ACTIVE_FILE_TRANSFERS:  # active file transfer
                            try:
                                ft = self._file_transfers[(message.get_friend_number(), message.get_file_number())]
                                ft.set_state_changed_handler(item.update_transfer_state)
                                ft.signal()
                            except:
                                print('Incoming not started transfer - no info found')
                    elif message.get_type() == MESSAGE_TYPE['INLINE']:  # inline
                        self.create_inline_item(message.get_data())
                    elif message.get_type() < 5:  # info message
                        data = message.get_data()
                        self.create_message_item(data[0],
                                                 data[2],
                                                 '',
                                                 data[3])
                    else:
                        data = message.get_data()
                        self.create_gc_message_item(data[0], data[2], data[1], data[4], data[3])
                self._messages.scrollToBottom()
                self._load_history = True
                if value in self._call:
                    self._screen.active_call()
                elif value in self._incoming_calls:
                    self._screen.incoming_call()
                else:
                    self._screen.call_finished()
            else:
                friend = self.get_curr_friend()

            self._screen.account_name.setText(friend.name)
            self._screen.account_status.setText(friend.status_message)
            self._screen.account_status.setToolTip(friend.get_full_status())
            avatar_path = friend.get_avatar_path()
            os.chdir(os.path.dirname(avatar_path))
            pixmap = QtGui.QPixmap(avatar_path)
            self._screen.account_avatar.setPixmap(pixmap.scaled(64, 64, QtCore.Qt.KeepAspectRatio,
                                                                QtCore.Qt.SmoothTransformation))
        except Exception as ex:  # no friend found. ignore
            util.log('Friend value: ' + str(value))
            util.log('Error in set active: ' + str(ex))
            raise

    def set_active_by_number_and_type(self, number, is_friend):
        for i in range(len(self._contacts)):
            c = self._contacts[i]
            if c.number == number and (type(c) is Friend == is_friend):
                self._active_friend = i
                break

    active_friend = property(get_active, set_active)

    def update(self):
        if self._active_friend + 1:
            self.set_active(self._active_friend)

    # -----------------------------------------------------------------------------------------------------------------
    # Filtration
    # -----------------------------------------------------------------------------------------------------------------

    def filtration_and_sorting(self, sorting=0, filter_str=''):
        """
        Filtration of friends list
        :param sorting: 0 - no sort, 1 - online only, 2 - online first, 4 - by name
        :param filter_str: show contacts which name contains this substring
        """
        filter_str = filter_str.lower()
        number = self.get_active_number()
        is_friend = self.is_active_a_friend()
        if sorting > 1:
            if sorting & 2:
                self._contacts = sorted(self._contacts, key=lambda x: int(x.status is not None), reverse=True)
            if sorting & 4:
                if not sorting & 2:
                    self._contacts = sorted(self._contacts, key=lambda x: x.name.lower())
                else:  # save results of prev sorting
                    online_friends = filter(lambda x: x.status is not None, self._contacts)
                    count = len(list(online_friends))
                    part1 = self._contacts[:count]
                    part2 = self._contacts[count:]
                    part1 = sorted(part1, key=lambda x: x.name.lower())
                    part2 = sorted(part2, key=lambda x: x.name.lower())
                    self._contacts = part1 + part2
            else:  # sort by number
                online_friends = filter(lambda x: x.status is not None, self._contacts)
                count = len(list(online_friends))
                part1 = self._contacts[:count]
                part2 = self._contacts[count:]
                part1 = sorted(part1, key=lambda x: x.number)
                part2 = sorted(part2, key=lambda x: x.number)
                self._contacts = part1 + part2
            self._screen.friends_list.clear()
            for contact in self._contacts:
                contact.set_widget(self.create_friend_item())
        for index, friend in enumerate(self._contacts):
            friend.visibility = (friend.status is not None or not (sorting & 1)) and (filter_str in friend.name.lower())
            friend.visibility = friend.visibility or friend.messages or friend.actions
            if friend.visibility:
                self._screen.friends_list.item(index).setSizeHint(QtCore.QSize(250, self._friend_item_height))
            else:
                self._screen.friends_list.item(index).setSizeHint(QtCore.QSize(250, 0))
        self._sorting, self._filter_string = sorting, filter_str
        self._settings['sorting'] = self._sorting
        self._settings.save()
        self.set_active_by_number_and_type(number, is_friend)

    def update_filtration(self):
        """
        Update list of contacts when 1 of friends change connection status
        """
        self.filtration_and_sorting(self._sorting, self._filter_string)

    def create_friend_item(self):
        """
        Method-factory
        :return: new widget for friend instance
        """
        return self._factory.friend_item()

    # -----------------------------------------------------------------------------------------------------------------
    # Friend getters
    # -----------------------------------------------------------------------------------------------------------------

    def get_friend_by_number(self, num):
        return list(filter(lambda x: x.number == num and type(x) is Friend, self._contacts))[0]

    def get_last_message(self):
        if self._active_friend + 1:
            return self.get_curr_friend().get_last_message_text()
        else:
            return ''

    def get_active_number(self):
        return self.get_curr_friend().number if self._active_friend + 1 else -1

    def get_active_name(self):
        return self.get_curr_friend().name if self._active_friend + 1 else ''

    def is_active_online(self):
        return self._active_friend + 1 and self.get_curr_friend().status is not None

    def new_name(self, number, name):
        friend = self.get_friend_by_number(number)
        tmp = friend.name
        friend.set_name(name)
        name = str(name, 'utf-8')
        if friend.name == name and tmp != name:
            message = QtWidgets.QApplication.translate("MainWindow", 'User {} is now known as {}')
            message = message.format(tmp, name)
            friend.append_message(InfoMessage(message, time.time()))
            friend.actions = True
            if number == self.get_active_number():
                self.create_message_item(message, time.time(), '', MESSAGE_TYPE['INFO_MESSAGE'])
                self._messages.scrollToBottom()
            self.set_active(None)

    # -----------------------------------------------------------------------------------------------------------------
    # Work with friends (remove, block, set alias, get public key)
    # -----------------------------------------------------------------------------------------------------------------

    def set_alias(self, num):
        """
        Set new alias for friend
        """
        friend = self._contacts[num]
        name = friend.name
        text = util_ui.tr("Enter new alias for friend {} or leave empty to use friend's name:").format(name)
        title = util_ui.tr('Set alias')
        text, ok = util_ui.text_dialog(text, title, name)
        if ok:
            aliases = self._settings['friends_aliases']
            if text:
                friend.name = bytes(text, 'utf-8')
                try:
                    index = list(map(lambda x: x[0], aliases)).index(friend.tox_id)
                    aliases[index] = (friend.tox_id, text)
                except:
                    aliases.append((friend.tox_id, text))
                friend.set_alias(text)
            else:  # use default name
                friend.name = bytes(self._tox.friend_get_name(friend.number), 'utf-8')
                friend.set_alias('')
                try:
                    index = list(map(lambda x: x[0], aliases)).index(friend.tox_id)
                    del aliases[index]
                except:
                    pass
            self._settings.save()
        if num == self.get_active_number() and self.is_active_a_friend():
            self.update()

    def friend_public_key(self, num):
        return self._contacts[num].tox_id

    def delete_friend(self, num):
        """
        Removes friend from contact list
        :param num: number of friend in list
        """
        friend = self._contacts[num]
        try:
            index = list(map(lambda x: x[0], self._settings['friends_aliases'])).index(friend.tox_id)
            del self._settings['friends_aliases'][index]
        except:
            pass
        if friend.tox_id in self._settings['notes']:
            del self._settings['notes'][friend.tox_id]
        self._settings.save()
        self.clear_history(num)
        if self._history.friend_exists_in_db(friend.tox_id):
            self._history.delete_friend_from_db(friend.tox_id)
        self._tox.friend_delete(friend.number)
        del self._contacts[num]
        self._screen.friends_list.takeItem(num)
        if num == self._active_friend:  # active friend was deleted
            if not len(self._contacts):  # last friend was deleted
                self.set_active(-1)
            else:
                self.set_active(0)
        data = self._tox.get_savedata()
        self._profile_manager.save_profile(data)

    def add_friend(self, tox_id):
        """
        Adds friend to list
        """
        num = self._tox.friend_add_norequest(tox_id)  # num - friend number
        item = self.create_friend_item()
        try:
            if not self._history.friend_exists_in_db(tox_id):
                self._history.add_friend_to_db(tox_id)
            message_getter = self._history.messages_getter(tox_id)
        except Exception as ex:  # something is wrong
            util.log('Accept friend request failed! ' + str(ex))
            message_getter = None
        friend = Friend(message_getter, num, tox_id, '', item, tox_id)
        self._contacts.append(friend)

    def block_user(self, tox_id):
        """
        Block user with specified tox id (or public key) - delete from friends list and ignore friend requests
        """
        tox_id = tox_id[:TOX_PUBLIC_KEY_SIZE * 2]
        if tox_id == self._tox.self_get_address[:TOX_PUBLIC_KEY_SIZE * 2]:
            return
        if tox_id not in self._settings['blocked']:
            self._settings['blocked'].append(tox_id)
            self._settings.save()
        try:
            num = self._tox.friend_by_public_key(tox_id)
            self.delete_friend(num)
            self.save_profile()
        except:  # not in friend list
            pass

    def unblock_user(self, tox_id, add_to_friend_list):
        """
        Unblock user
        :param tox_id: tox id of contact
        :param add_to_friend_list: add this contact to friend list or not
        """
        self._settings['blocked'].remove(tox_id)
        self._settings.save()
        if add_to_friend_list:
            self.add_friend(tox_id)
            self.save_profile()

    # -----------------------------------------------------------------------------------------------------------------
    # Friend requests
    # -----------------------------------------------------------------------------------------------------------------

    def send_friend_request(self, tox_id, message):
        """
        Function tries to send request to contact with specified id
        :param tox_id: id of new contact or tox dns 4 value
        :param message: additional message
        :return: True on success else error string
        """
        try:
            message = message or 'Hello! Add me to your contact list please'
            if '@' in tox_id:  # value like groupbot@toxme.io
                tox_id = tox_dns(tox_id)
                if tox_id is None:
                    raise Exception('TOX DNS lookup failed')
            if len(tox_id) == TOX_PUBLIC_KEY_SIZE * 2:  # public key
                self.add_friend(tox_id)
                title = util_ui.tr('Friend added')
                text = util_ui.tr('Friend added without sending friend request')
                util_ui.message_box(text, title)
            else:
                result = self._tox.friend_add(tox_id, message.encode('utf-8'))
                tox_id = tox_id[:TOX_PUBLIC_KEY_SIZE * 2]
                item = self.create_friend_item()
                if not self._history.friend_exists_in_db(tox_id):
                    self._history.add_friend_to_db(tox_id)
                message_getter = self._history.messages_getter(tox_id)
                friend = Friend(message_getter, result, tox_id, '', item, tox_id)
                self._contacts.append(friend)
            self.save_profile()
            return True
        except Exception as ex:  # wrong data
            util.log('Friend request failed with ' + str(ex))
            return str(ex)

    def process_friend_request(self, tox_id, message):
        """
        Accept or ignore friend request
        :param tox_id: tox id of contact
        :param message: message
        """
        if tox_id in self._settings['blocked']:
            return
        try:
            text = util_ui.tr('User {} wants to add you to contact list. Message:\n{}')
            reply = util_ui.question(text.format(tox_id, message), util_ui.tr('Friend request'))
            if reply:  # accepted
                self.add_friend(tox_id)
                data = self._tox.get_savedata()
                self._profile_manager.save_profile(data)
        except Exception as ex:  # something is wrong
            util.log('Accept friend request failed! ' + str(ex))

    # -----------------------------------------------------------------------------------------------------------------
    # Typing notifications
    # -----------------------------------------------------------------------------------------------------------------

    def send_typing(self, typing):
        """
        Send typing notification to a friend
        """
        if self._settings['typing_notifications'] and self._active_friend + 1:
            try:
                friend = self.get_curr_friend()
                if friend.status is not None:
                    self._tox.self_set_typing(friend.number, typing)
            except:
                pass

    def friend_typing(self, friend_number, typing):
        """
        Display incoming typing notification
        """
        if friend_number == self.get_active_number() and self.is_active_a_friend():
            self._screen.typing.setVisible(typing)
